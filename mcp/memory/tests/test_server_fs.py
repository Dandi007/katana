"""Governed Full VFS (fs_*) parity for the Memory app (design §5.2, INV-5).

Anchors that Memory exposes the governed fs_* façade scoped to a tenant subtree
and that fs_* mutations flow through the policy → transaction pipeline (a real
Git commit), never a raw bypass.
"""
import asyncio
import subprocess

import pytest
from fastmcp import Client

from katana_memory_mcp import server, store


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    d = tmp_path / "uther"
    d.mkdir()
    return str(tmp_path), str(d)


def _call(mcp, tool, args=None):
    async def go():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args or {})).data
    return asyncio.run(go())


@pytest.fixture
def srv(tmp_path):
    repo, tdir = _init_repo(tmp_path)
    return server.build_tenant_server("uther", tdir, repo), tdir, repo


def _tool_names(mcp):
    async def go():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}
    return asyncio.run(go())


def test_seven_domain_tools_plus_full_fs_facade(srv):
    mcp, _, _ = srv
    names = _tool_names(mcp)
    from katana_kb_mcp_shared.kernel.facade import FS_FACADE
    domain = {"memory_index", "memory_get", "memory_create", "memory_update",
              "memory_delete", "memory_read", "memory_edit"}
    assert domain <= names, f"missing domain tools: {domain - names}"
    # The COMPLETE governed Full VFS surface is exposed (design §5.2), not a
    # five-tool subset.
    assert FS_FACADE <= names, f"missing fs_* tools: {FS_FACADE - names}"


def test_fs_create_read_edit_roundtrip(srv):
    mcp, _, repo = srv
    card = ("---\nid: m-aaaaaa\nname: fs-card\ndescription: d\nstatus: active\n"
            "---\n\n## Fact\nx\n\n## How to Verify\ny\n")
    r = _call(mcp, "fs_create", {"virtual_path": "fs-card.md", "content": card})
    assert r["commit_sha"]
    assert r["resource_id"].startswith("m-")
    rd = _call(mcp, "fs_read", {"virtual_path": "fs-card.md"})
    assert "## Fact" in rd["content"]
    _call(mcp, "fs_edit", {"virtual_path": "fs-card.md",
                           "old_string": "## Fact\nx", "new_string": "## Fact\nz"})
    rd2 = _call(mcp, "fs_read", {"virtual_path": "fs-card.md"})
    # cat -n rendering: the edited body line is present
    assert "\tz" in rd2["content"]


def test_fs_create_invalid_card_is_rejected_by_policy(srv):
    mcp, _, _ = srv
    with pytest.raises(Exception):
        _call(mcp, "fs_create", {"virtual_path": "bad.md",
                                 "content": "not a card\n"})


def test_fs_traversal_rejected(srv):
    mcp, _, _ = srv
    with pytest.raises(Exception):
        _call(mcp, "fs_read", {"virtual_path": "../../etc/passwd"})


def test_fs_list_hides_reserved(srv):
    mcp, _, _ = srv
    card = ("---\nid: m-bbbbbb\nname: c\ndescription: d\nstatus: active\n---\n\n"
            "## Fact\nx\n\n## How to Verify\ny\n")
    _call(mcp, "fs_create", {"virtual_path": "c.md", "content": card})
    listing = [n["virtual_path"] for n in _call(mcp, "fs_list", {})]
    assert all(not p.split("/")[-1].startswith(".git") for p in listing)
