"""Canonical identity invariants for Memory (design §5.3/§5.6, operator P0 #6).

- catalog resource_id == card frontmatter id after create/update/rename;
- copy mints a NEW id (and would need a new frontmatter id to commit);
- a tombstoned id is never re-bound/reused.
"""
import asyncio
import json
import subprocess

import pytest
from fastmcp import Client

from katana_memory_mcp import server
from katana_kb_mcp_shared.kernel.catalog import IdentityError


def _init(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "uther").mkdir()
    return str(tmp_path), str(tmp_path / "uther")


def _call(mcp, tool, args=None):
    async def go():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args or {})).data
    return asyncio.run(go())


@pytest.fixture
def srv(tmp_path):
    repo, tdir = _init(tmp_path)
    return server.build_tenant_server("uther", tdir, repo), tdir, repo


def _catalog(repo):
    blob = subprocess.run(["git", "-C", repo, "show", "HEAD:.kb/catalog.json"],
                          capture_output=True, text=True).stdout
    return json.loads(blob)


def test_catalog_id_equals_frontmatter_id_through_lifecycle(srv):
    mcp, _, repo = srv
    created = _call(mcp, "memory_create",
                    {"name": "life-card", "description": "d", "body": "b"})
    cid = created["id"]
    cat = _catalog(repo)["by_id"]
    assert cid in cat and cat[cid].endswith("life-card.md")
    # rename via update(name) keeps the same id bound to the new path.
    _call(mcp, "memory_update", {"id": cid, "name": "life-card-2"})
    cat = _catalog(repo)["by_id"]
    assert cat[cid].endswith("life-card-2.md")


def test_delete_tombstones_and_id_never_reused(srv):
    mcp, _, repo = srv
    cid = _call(mcp, "memory_create",
                {"name": "gone", "description": "d", "body": "b"})["id"]
    _call(mcp, "memory_delete", {"id": cid})
    cat = _catalog(repo)
    assert cid in cat["tombstones"]
    assert cid not in cat["by_id"]


def test_tombstoned_id_rebind_is_rejected(srv):
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create",
                {"name": "temp", "description": "d", "body": "b"})["id"]
    _call(mcp, "memory_delete", {"id": cid})
    # Directly attempting to re-bind the tombstoned id fails closed.
    from katana_kb_mcp_shared.kernel.catalog import Catalog
    from katana_memory_mcp.policy import ID_PREFIX
    cat = Catalog(repo, id_prefix=ID_PREFIX)
    with pytest.raises(IdentityError):
        cat.bind(cid, "uther/temp.md")


def test_fs_create_with_frontmatter_id_binds_that_id(srv):
    mcp, _, repo = srv
    card = ("---\nid: m-abc123\nname: fixed\ndescription: d\nstatus: active\n"
            "---\n\n## Fact\nx\n\n## How to Verify\ny\n")
    r = _call(mcp, "fs_create", {"virtual_path": "fixed.md", "content": card})
    # The catalog adopts the frontmatter id (no split, operator P0 #6).
    assert r["resource_id"] == "m-abc123"
    assert _catalog(repo)["by_id"]["m-abc123"].endswith("fixed.md")
