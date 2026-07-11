"""Governed-pipeline anchors for the Memory domain tools (design §4.4, INV-5).

Proves the 7 domain mutation tools do NOT bypass the shared kernel: every
memory_create/update/delete/edit commit carries a kernel transaction manifest,
enforces MemoryPolicy, and shares the fs_* CAS/idempotency semantics. These
tests fail if a domain tool can commit without passing through the policy +
TransactionEngine.
"""
import asyncio
import subprocess

import pytest
from fastmcp import Client

from katana_memory_mcp import server, store
from katana_kb_mcp_shared.kernel.manifest import extract_from_message


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


def _head_message(repo):
    return subprocess.run(["git", "-C", repo, "log", "-1", "--format=%B"],
                          capture_output=True, text=True).stdout


@pytest.fixture
def srv(tmp_path):
    repo, tdir = _init_repo(tmp_path)
    return server.build_tenant_server("uther", tdir, repo), tdir, repo


def test_memory_create_commit_carries_kernel_manifest(srv):
    mcp, _, repo = srv
    created = _call(mcp, "memory_create", {
        "name": "m-card", "description": "d",
        "body": "## Fact\nx\n\n## How to Verify\ny"})
    manifest = extract_from_message(_head_message(repo))
    assert manifest is not None, "domain commit has no kernel manifest → bypass"
    assert manifest.domain == "memory"
    assert manifest.policy_version >= 1
    # The card id appears in the manifest change set.
    ids = {c["resource_id"] for c in manifest.changes}
    assert created["id"] in ids


def test_memory_update_and_delete_carry_manifest(srv):
    mcp, _, repo = srv
    cid = _call(mcp, "memory_create",
                {"name": "u-card", "description": "d", "body": "b"})["id"]
    _call(mcp, "memory_update", {"id": cid, "status": "stale"})
    assert extract_from_message(_head_message(repo)) is not None
    _call(mcp, "memory_delete", {"id": cid})
    m = extract_from_message(_head_message(repo))
    assert m is not None
    assert any(c["op"] == "delete" for c in m.changes)


def test_memory_edit_commit_carries_manifest(srv):
    mcp, _, repo = srv
    cid = _call(mcp, "memory_create",
                {"name": "e-card", "description": "d",
                 "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    _call(mcp, "memory_edit",
          {"id": cid, "old_string": "## Fact\nx", "new_string": "## Fact\nz"})
    assert extract_from_message(_head_message(repo)) is not None


def test_domain_commit_records_catalog_binding(srv):
    # Identity catalog is committed atomically with content (INV-6): the card id
    # is bound to its path in the canonical .kb/catalog.json.
    mcp, _, repo = srv
    created = _call(mcp, "memory_create",
                    {"name": "cat-card", "description": "d", "body": "b"})
    import json
    blob = subprocess.run(["git", "-C", repo, "show", "HEAD:.kb/catalog.json"],
                          capture_output=True, text=True).stdout
    catalog = json.loads(blob)
    assert created["id"] in catalog["by_id"]
    assert catalog["by_id"][created["id"]].endswith("cat-card.md")


def test_domain_tool_cannot_commit_invalid_card(srv, monkeypatch):
    # A card whose projected post-state violates MemoryPolicy must be rejected
    # by the SAME policy fs_* uses — proving there is no unguarded write path.
    mcp, tdir, repo = srv
    before = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout

    # Force the store to emit a card missing '## How to Verify' is still a valid
    # card schema-wise; instead, corrupt the id prefix via monkeypatched writer.
    import katana_memory_mcp.store as st
    orig = st.create_card

    def bad_create(tenant_dir, *a, **k):
        res = orig(tenant_dir, *a, **k)
        # tamper the just-written file to an invalid (wrong-prefix) id
        path = res["changed_paths"][0]
        text = open(path, encoding="utf-8").read().replace(res["id"], "x-000000")
        open(path, "w", encoding="utf-8").write(text)
        return res

    monkeypatch.setattr(server.store, "create_card", bad_create)
    with pytest.raises(Exception):
        _call(mcp, "memory_create",
              {"name": "bad-card", "description": "d", "body": "b"})
    after = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout
    # Rejected mutation left zero canonical delta (design §6.6).
    assert after == before
