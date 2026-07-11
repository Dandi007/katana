"""Multi-tenant catalog + isolation anchors (operator P0 #5).

Two tenant servers share one Memory data repo (one .kb/catalog.json). Interleaved
mutations must both survive in the catalog (no stale-instance clobber), and no
tenant may read/list/glob/change another tenant's cards through any fs_* op.
"""
import asyncio
import json
import subprocess

import pytest
from fastmcp import Client

from katana_memory_mcp import server


def _init(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    for t in ("alice", "bob"):
        (tmp_path / t).mkdir()
    return str(tmp_path)


def _call(mcp, tool, args=None):
    async def go():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args or {})).data
    return asyncio.run(go())


@pytest.fixture
def two_tenants(tmp_path):
    root = _init(tmp_path)
    alice = server.build_tenant_server("alice", str(tmp_path / "alice"), root)
    bob = server.build_tenant_server("bob", str(tmp_path / "bob"), root)
    return root, alice, bob, tmp_path


def test_interleaved_creates_both_survive_in_catalog(two_tenants):
    root, alice, bob, tmp_path = two_tenants
    a1 = _call(alice, "memory_create",
               {"name": "a-one", "description": "d", "body": "b"})
    b1 = _call(bob, "memory_create",
               {"name": "b-one", "description": "d", "body": "b"})
    a2 = _call(alice, "memory_create",
               {"name": "a-two", "description": "d", "body": "b"})
    b2 = _call(bob, "memory_create",
               {"name": "b-two", "description": "d", "body": "b"})

    blob = subprocess.run(["git", "-C", root, "show", "HEAD:.kb/catalog.json"],
                          capture_output=True, text=True).stdout
    catalog = json.loads(blob)["by_id"]
    # All four bindings coexist — no tenant clobbered the other's commits.
    for cid in (a1["id"], b1["id"], a2["id"], b2["id"]):
        assert cid in catalog
    assert catalog[a1["id"]].startswith("alice/")
    assert catalog[b1["id"]].startswith("bob/")


def test_tenant_cannot_read_or_list_other_tenant(two_tenants):
    root, alice, bob, tmp_path = two_tenants
    _call(bob, "memory_create",
          {"name": "secret", "description": "d", "body": "b"})
    _call(alice, "memory_create",
          {"name": "a-doc", "description": "d", "body": "b"})
    # alice lists her own subtree only.
    listing = _call(alice, "fs_list", {})
    paths = {n["virtual_path"] for n in listing}
    assert all(not p.startswith("bob/") for p in paths)
    # alice cannot read bob's card via fs_read.
    with pytest.raises(Exception):
        _call(alice, "fs_read", {"virtual_path": "../bob/secret.md"})


def test_tenant_glob_scoped(two_tenants):
    root, alice, bob, tmp_path = two_tenants
    _call(alice, "memory_create",
          {"name": "mine", "description": "d", "body": "b"})
    _call(bob, "memory_create",
          {"name": "yours", "description": "d", "body": "b"})
    hits = _call(alice, "fs_glob", {"pattern": "alice/*.md"})
    paths = {n["virtual_path"] for n in hits}
    assert any(p.endswith("mine.md") for p in paths)
    assert all(not p.startswith("bob/") for p in paths)


def test_tenant_all_fs_ops_cannot_touch_other_tenant(two_tenants):
    root, alice, bob, tmp_path = two_tenants
    _call(alice, "memory_create",
          {"name": "mine", "description": "d", "body": "b"})
    _call(bob, "memory_create",
          {"name": "secret", "description": "d", "body": "b"})

    attacks = [
        ("fs_read", {"virtual_path": "bob/secret.md"}),
        ("fs_stat", {"virtual_path": "bob/secret.md"}),
        ("fs_resolve", {"virtual_path": "bob/secret.md"}),
        ("fs_write", {"virtual_path": "bob/secret.md", "content": "x"}),
        ("fs_edit", {"virtual_path": "bob/secret.md", "old_string": "b", "new_string": "x"}),
        ("fs_copy", {"virtual_path": "mine.md", "new_path": "bob/copy.md"}),
        ("fs_rename", {"virtual_path": "mine.md", "new_path": "bob/renamed.md"}),
        ("fs_delete", {"virtual_path": "bob/secret.md"}),
        ("fs_batch", {"changes": [{"op": "delete", "virtual_path": "bob/secret.md"}]}),
    ]
    for tool, args in attacks:
        with pytest.raises(Exception):
            _call(alice, tool, args)

    hits = _call(alice, "fs_glob", {"pattern": "**/*.md"})
    assert all(not n["virtual_path"].startswith("bob/") for n in hits)
    changes = _call(alice, "fs_changes")
    assert "bob/" not in json.dumps(changes, ensure_ascii=False)
    assert (tmp_path / "bob" / "secret.md").exists()
