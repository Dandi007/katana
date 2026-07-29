"""fs_* Full VFS contract tests for Memory app.

Covers every operation's success/error envelopes, invariants,
and edge cases per spec §5.2, §5.5, §5.6, §5.9.
"""

import asyncio
import os
import re
import subprocess

import pytest
from fastmcp import Client

from katana_kernel import MutationBrokenError
from katana_memory_mcp import server, store
from katana_memory_mcp.fs_tools import FSTools

CARD_CONTENT = """---
name: test-card
description: A test card
status: active
last_verified: 2026-07-08
metadata:
  type: reference
---

## Fact

Some fact content.

## How to Verify

Run a test.
"""

CARD_CONTENT_TWO = """---
name: test-card-two
description: Another test card
status: active
last_verified: 2026-07-08
metadata:
  type: reference
---

## Fact

Another fact.

## How to Verify

Run another test.
"""


def _card(name, description="A test card", body_fact="Some fact content."):
    return f"""---
name: {name}
description: {description}
status: active
last_verified: 2026-07-08
metadata:
  type: reference
---

## Fact

{body_fact}

## How to Verify

Run a test.
"""


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


def _memory_policy():
    from katana_kernel import DomainPolicy
    def _invariants(domain, op, args):
        if op in ("create", "update", "edit"):
            body = args.get("body")
            if body is not None:
                if not re.search(r'^## Fact\b', body, re.MULTILINE):
                    raise ValueError("body must contain '## Fact' section")
                if not re.search(r'^## How to Verify\b', body, re.MULTILINE):
                    raise ValueError("body must contain '## How to Verify' section")
        if op == "create" and not args.get("body"):
            raise ValueError("body is required for create")
        if op.startswith("fs_") and op not in ("fs_batch", "fs_capabilities", "fs_resolve",
                                                  "fs_stat", "fs_list", "fs_glob", "fs_read"):
            content = args.get("content")
            if content is not None:
                if not re.search(r'^## Fact\b', content, re.MULTILINE):
                    raise ValueError("content must contain '## Fact' section")
                if not re.search(r'^## How to Verify\b', content, re.MULTILINE):
                    raise ValueError("content must contain '## How to Verify' section")
    return DomainPolicy(
        domain="memory",
        allowed_ops={
            "create", "update", "delete", "edit", "list", "get", "read",
            "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename",
            "fs_delete", "fs_batch",
        },
        invariants=[_invariants],
    )


@pytest.fixture
def srv(tmp_path):
    repo, tdir = _init_repo(tmp_path)
    return server.build_tenant_server("uther", tdir, repo), tdir, repo


@pytest.fixture
def tools(tmp_path):
    repo, tdir = _init_repo(tmp_path)
    from katana_kernel import (
        GovernedKernel,
        GovernedVFS,
        ResourceIdLedger,
        TransactionManifest,
    )
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(repo, ".katana", "manifests"))
    policy = _memory_policy()
    kernel.bind("memory", policy, vfs, ledger, manifest, repo)
    return FSTools(kernel, "uther", repo)


# ── fs_capabilities ──────────────────────────────────────────────────────────

def test_fs_capabilities_success_envelope(tools):
    result = tools.fs_capabilities()
    assert result["node_type"] == "capabilities"
    assert "capabilities" in result
    assert "operations" in result["capabilities"]
    assert "fs_capabilities" in result["capabilities"]["operations"]
    assert "fs_create" in result["capabilities"]["operations"]
    assert "fs_batch" in result["capabilities"]["operations"]
    assert "commit" in result


def test_fs_capabilities_via_mcp(srv):
    mcp, tdir, repo = srv
    result = _call(mcp, "fs_capabilities")
    assert "capabilities" in result
    assert "fs_read" in result["capabilities"]["operations"]


def test_fs_create_broken_is_machine_readable_and_never_success(
    tools, monkeypatch,
):
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["uther/broken.md"]},
    )

    def _raise_broken(*args, **kwargs):
        raise broken

    monkeypatch.setattr(tools._kernel, "mutate", _raise_broken)
    result = tools.fs_create("uther/broken.md", _card("broken"))

    assert result["code"] == "BROKEN"
    assert result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert result["manual_recovery_required"] is True
    assert "git" not in result


def test_memory_server_broken_envelope_is_not_success():
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["uther/broken.md"]},
    )
    result = server._server_mutation(lambda: (_ for _ in ()).throw(broken))

    assert result["code"] == result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert "git" not in result


# ── fs_resolve ───────────────────────────────────────────────────────────────

def test_fs_resolve_by_path(tools):
    tools.fs_create("uther/test-resolve.md", _card("test-resolve"))
    result = tools.fs_resolve("uther/test-resolve.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-resolve.md"
    assert result["resource_id"] is not None
    assert store.ID_RE.fullmatch(result["resource_id"])
    assert result["content_hash"] is not None
    assert result["content_hash"].startswith("sha256:")
    assert result["resource_revision"] is not None
    assert result["content_revision"] is not None
    assert result["commit"] is not None


def test_fs_resolve_by_id(tools):
    tools.fs_create("uther/test-resolve-id.md", _card("test-resolve-id"))
    stat = tools.fs_stat("uther/test-resolve-id.md")
    rid = stat["resource_id"]
    result = tools.fs_resolve(rid)
    assert result["resource_id"] == rid
    assert result["virtual_path"] == "uther/test-resolve-id.md"


def test_fs_resolve_not_found(tools):
    result = tools.fs_resolve("uther/nonexistent.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert result["virtual_path"] == "uther/nonexistent.md"


def test_fs_resolve_bad_id(tools):
    result = tools.fs_resolve("m-ffffff")
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert result["resource_id"] == "m-ffffff"


def test_fs_resolve_tombstoned_returns_replaced(tools):
    tools.fs_create("uther/tomb-resolve.md", _card("tomb-resolve"))
    stat = tools.fs_stat("uther/tomb-resolve.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/tomb-resolve.md")
    result = tools.fs_resolve(rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── fs_stat ──────────────────────────────────────────────────────────────────

def test_fs_stat_file_success_envelope(tools):
    tools.fs_create("uther/test-stat.md", _card("test-stat"))
    result = tools.fs_stat("uther/test-stat.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-stat.md"
    assert result["resource_id"] is not None
    assert store.ID_RE.fullmatch(result["resource_id"])
    assert result["size"] > 0
    assert result["media_type"] == "text/markdown"
    assert result["content_hash"] is not None
    assert result["content_hash"].startswith("sha256:")
    assert result["resource_revision"] is not None
    assert result["content_revision"] is not None
    assert result["commit"] is not None


def test_fs_stat_directory(tools):
    result = tools.fs_stat("uther")
    assert result["node_type"] == "directory"
    assert result["virtual_path"] == "uther/"
    assert result["resource_id"] is None
    assert result["size"] is None
    assert result["media_type"] is None


def test_fs_stat_not_found(tools):
    result = tools.fs_stat("uther/nope.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_stat_path_traversal_rejected(tools):
    result = tools.fs_stat("../etc/passwd")
    assert result["code"] == "INVALID_PATH"


# ── fs_list ──────────────────────────────────────────────────────────────────

def test_fs_list_directory(tools):
    tools.fs_create("uther/test-list-1.md", _card("test-list-1"))
    tools.fs_create("uther/test-list-2.md", _card("test-list-2"))
    result = tools.fs_list("uther")
    assert result["node_type"] == "directory"
    assert "entries" in result
    paths = [e["virtual_path"] for e in result["entries"]]
    assert "uther/test-list-1.md" in paths
    assert "uther/test-list-2.md" in paths
    for entry in result["entries"]:
        assert entry["node_type"] == "file"
        assert entry["resource_id"] is not None
        assert entry["size"] > 0
        assert entry["media_type"] == "text/markdown"
        assert entry["content_hash"] is not None


def test_fs_list_empty_root(tools):
    result = tools.fs_list("")
    assert result["node_type"] == "directory"
    assert "entries" in result


def test_fs_list_not_directory(tools):
    tools.fs_create("uther/test-list-file.md", _card("test-list-file"))
    result = tools.fs_list("uther/test-list-file.md")
    assert result["code"] == "INVALID_PATH"


# ── fs_glob ──────────────────────────────────────────────────────────────────

def test_fs_glob_pattern(tools):
    tools.fs_create("uther/test-glob-a.md", _card("test-glob-a"))
    tools.fs_create("uther/test-glob-b.md", _card("test-glob-b"))
    tools.fs_create("uther/other-glob.md", _card("other-glob"))
    result = tools.fs_glob("uther/test-glob-*.md")
    assert "hits" in result
    assert len(result["hits"]) == 2
    assert "entries" in result
    assert len(result["entries"]) == 2


def test_fs_glob_no_match(tools):
    result = tools.fs_glob("uther/nonexistent-*.md")
    assert result["hits"] == []
    assert result["entries"] == []


# ── fs_read ──────────────────────────────────────────────────────────────────

def test_fs_read_success_envelope(tools):
    tools.fs_create("uther/test-read.md", _card("test-read"))
    result = tools.fs_read("uther/test-read.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-read.md"
    assert result["resource_id"] is not None
    assert result["size"] > 0
    assert result["content_hash"] is not None
    assert "content" in result
    assert "## Fact" in result["content"]
    assert "total_lines" in result
    assert result["total_lines"] > 0


def test_fs_read_not_found(tools):
    result = tools.fs_read("uther/nope.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_read_offset_limit(tools):
    tools.fs_create("uther/test-read-ol.md", _card("test-read-ol"))
    result = tools.fs_read("uther/test-read-ol.md", offset=1, limit=3)
    assert result["offset"] == 1
    assert result["limit"] == 3
    lines = result["content"].split("\n")
    assert len(lines) <= 3


# ── fs_create ────────────────────────────────────────────────────────────────

def test_fs_create_success_envelope(tools):
    result = tools.fs_create("uther/test-create.md", _card("test-create"))
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-create.md"
    assert result["resource_id"] is not None
    assert store.ID_RE.fullmatch(result["resource_id"])
    assert result["size"] > 0
    assert result["media_type"] == "text/markdown"
    assert result["content_hash"] is not None
    assert result["content_hash"].startswith("sha256:")
    assert result["resource_revision"] is not None
    assert result["content_revision"] is not None
    assert result["commit"] is not None
    assert "git" in result
    assert result["git"]["committed"] is True


def test_fs_create_generates_new_id(tools):
    c1 = tools.fs_create("uther/test-create-id-1.md", _card("test-create-id-1"))
    c2 = tools.fs_create("uther/test-create-id-2.md", _card("test-create-id-2"))
    assert c1["resource_id"] != c2["resource_id"]
    assert store.ID_RE.fullmatch(c1["resource_id"])
    assert store.ID_RE.fullmatch(c2["resource_id"])


def test_fs_create_injects_id_into_content(tools):
    result = tools.fs_create("uther/test-create-content.md", _card("test-create-content"))
    rid = result["resource_id"]
    content = tools.fs_read("uther/test-create-content.md")["content"]
    assert f"id: {rid}" in content


def test_fs_create_duplicate_path_rejected(tools):
    tools.fs_create("uther/test-create-dup.md", _card("test-create-dup"))
    result = tools.fs_create("uther/test-create-dup.md", _card("test-create-dup"))
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_create_invalid_content_rejected(tools):
    result = tools.fs_create("uther/test-create-bad.md", "not valid card content")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_missing_fact_rejected(tools):
    bad = """---
name: no-fact
description: missing fact section
status: active
---

## How to Verify

Nothing.
"""
    result = tools.fs_create("uther/no-fact.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_via_mcp(srv):
    mcp, tdir, repo = srv
    result = _call(mcp, "fs_create", {
        "path": "uther/test-mcp-create.md",
        "content": _card("test-mcp-create"),
    })
    assert result["resource_id"] is not None
    assert store.ID_RE.fullmatch(result["resource_id"])


def test_fs_create_filename_name_mismatch_rejected(tools):
    result = tools.fs_create("uther/wrong-name.md", CARD_CONTENT)
    assert result["code"] == "INVALID_CONTENT"
    assert "does not match" in result["message"]


def test_fs_create_content_too_large(tools):
    big = "x" * 1_100_000
    result = tools.fs_create("uther/big.md", big)
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_fs_create_with_explicit_resource_id(tools):
    result = tools.fs_create("uther/test-create-explicit-id.md", _card("test-create-explicit-id"),
                             resource_id="m-abc123")
    assert result["resource_id"] == "m-abc123"
    content = tools.fs_read("uther/test-create-explicit-id.md")["content"]
    assert "id: m-abc123" in content


def test_fs_create_explicit_id_already_exists(tools):
    tools.fs_create("uther/test-create-dup-id.md", _card("test-create-dup-id"),
                    resource_id="m-abc456")
    result = tools.fs_create("uther/test-create-dup-id-2.md", _card("test-create-dup-id-2"),
                             resource_id="m-abc456")
    assert result["code"] == "REF_MISMATCH"


def test_fs_create_explicit_id_tombstoned(tools):
    tools.fs_create("uther/tomb-create-id.md", _card("tomb-create-id"))
    stat = tools.fs_stat("uther/tomb-create-id.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/tomb-create-id.md")
    result = tools.fs_create("uther/tomb-create-id-2.md", _card("tomb-create-id-2"),
                             resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── fs_write (no implicit create) ────────────────────────────────────────────

def test_fs_write_success_envelope(tools):
    tools.fs_create("uther/test-write.md", _card("test-write"))
    modified = _card("test-write", body_fact="Updated fact content.")
    result = tools.fs_write("uther/test-write.md", modified)
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-write.md"
    assert result["resource_id"] is not None
    assert result["size"] > 0
    assert "Updated fact content." in result["content"]
    assert result["git"]["committed"] is True


def test_fs_write_no_implicit_create(tools):
    result = tools.fs_write("uther/no-such-file.md", _card("no-such-file"))
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert "does not implicitly create" in result["message"]


def test_fs_write_id_immutable(tools):
    tools.fs_create("uther/test-write-id.md", _card("test-write-id"))
    stat = tools.fs_stat("uther/test-write-id.md")
    original_id = stat["resource_id"]
    modified = _card("test-write-id").replace("name: test-write-id", "name: test-write-id\nid: m-999999")
    result = tools.fs_write("uther/test-write-id.md", modified)
    assert result["code"] == "REF_MISMATCH"


def test_fs_write_invalid_content_rejected(tools):
    tools.fs_create("uther/test-write-bad.md", _card("test-write-bad"))
    result = tools.fs_write("uther/test-write-bad.md", "not valid")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_write_revision_conflict(tools):
    tools.fs_create("uther/rev-conflict.md", _card("rev-conflict"))
    stat = tools.fs_stat("uther/rev-conflict.md")
    old_rev = stat["resource_revision"]
    modified = _card("rev-conflict", body_fact="First update.")
    tools.fs_write("uther/rev-conflict.md", modified)
    result = tools.fs_write("uther/rev-conflict.md", _card("rev-conflict"),
                            expected_resource_revision=old_rev)
    assert result["code"] == "REVISION_CONFLICT"
    assert result["retryable"] is True


def test_fs_write_resource_replaced_after_delete(tools):
    tools.fs_create("uther/repl-write.md", _card("repl-write"))
    stat = tools.fs_stat("uther/repl-write.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/repl-write.md")
    result = tools.fs_write("uther/repl-write.md", _card("repl-write"))
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_write_with_resource_id_match(tools):
    tools.fs_create("uther/test-write-rid.md", _card("test-write-rid"))
    stat = tools.fs_stat("uther/test-write-rid.md")
    rid = stat["resource_id"]
    modified = _card("test-write-rid", body_fact="Updated with rid.")
    result = tools.fs_write("uther/test-write-rid.md", modified, resource_id=rid)
    assert result["node_type"] == "file"


def test_fs_write_with_resource_id_mismatch(tools):
    tools.fs_create("uther/test-write-rid-mm.md", _card("test-write-rid-mm"))
    modified = _card("test-write-rid-mm", body_fact="Updated.")
    result = tools.fs_write("uther/test-write-rid-mm.md", modified, resource_id="m-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_write_with_resource_id_tombstoned(tools):
    tools.fs_create("uther/test-write-rid-tomb.md", _card("test-write-rid-tomb"))
    stat = tools.fs_stat("uther/test-write-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/test-write-rid-tomb.md")
    result = tools.fs_write("uther/test-write-rid-tomb.md", _card("test-write-rid-tomb"),
                            resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── fs_edit (exact-match) ────────────────────────────────────────────────────

def test_fs_edit_success_envelope(tools):
    tools.fs_create("uther/test-edit.md", _card("test-edit"))
    result = tools.fs_edit("uther/test-edit.md", "Some fact content.", "Edited fact content.")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-edit.md"
    assert "Edited fact content." in result["content"]
    assert result["git"]["committed"] is True


def test_fs_edit_exact_match_required(tools):
    tools.fs_create("uther/test-edit-exact.md", _card("test-edit-exact"))
    result = tools.fs_edit("uther/test-edit-exact.md", "nonexistent string", "replacement")
    assert result["code"] == "INVALID_CONTENT"
    assert "not found" in result["message"]


def test_fs_edit_non_unique_requires_replace_all(tools):
    content = """---
name: edit-dup
description: Here is some text
status: active
last_verified: 2026-07-08
metadata:
  type: reference
---

## Fact

Here is some fact.

## How to Verify

Here is some verification.
"""
    tools.fs_create("uther/edit-dup.md", content)
    result = tools.fs_edit("uther/edit-dup.md", "Here is", "There is")
    assert result["code"] == "INVALID_CONTENT"
    assert "matches" in result["message"]

    result2 = tools.fs_edit("uther/edit-dup.md", "Here is", "There is", replace_all=True)
    assert "There is" in result2["content"]
    assert "Here is" not in result2["content"]


def test_fs_edit_id_immutable(tools):
    tools.fs_create("uther/test-edit-id.md", _card("test-edit-id"))
    stat = tools.fs_stat("uther/test-edit-id.md")
    original_id = stat["resource_id"]
    result = tools.fs_edit("uther/test-edit-id.md",
                           f"name: test-edit-id", f"name: test-edit-id\nid: m-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_edit_old_string_empty_rejected(tools):
    tools.fs_create("uther/test-edit-empty.md", _card("test-edit-empty"))
    result = tools.fs_edit("uther/test-edit-empty.md", "", "x")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_noop_rejected(tools):
    tools.fs_create("uther/test-edit-noop.md", _card("test-edit-noop"))
    result = tools.fs_edit("uther/test-edit-noop.md", "## Fact", "## Fact")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_revision_conflict(tools):
    tools.fs_create("uther/edit-rev.md", _card("edit-rev"))
    stat = tools.fs_stat("uther/edit-rev.md")
    old_rev = stat["resource_revision"]
    tools.fs_edit("uther/edit-rev.md", "Some fact content.", "First edit.")
    result = tools.fs_edit("uther/edit-rev.md", "First edit.", "Second edit.",
                           expected_resource_revision=old_rev)
    assert result["code"] == "REVISION_CONFLICT"


def test_fs_edit_with_resource_id_match(tools):
    tools.fs_create("uther/test-edit-rid.md", _card("test-edit-rid"))
    stat = tools.fs_stat("uther/test-edit-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_edit("uther/test-edit-rid.md", "Some fact content.", "RID edited.",
                           resource_id=rid)
    assert result["node_type"] == "file"


def test_fs_edit_with_resource_id_mismatch(tools):
    tools.fs_create("uther/test-edit-rid-mm.md", _card("test-edit-rid-mm"))
    result = tools.fs_edit("uther/test-edit-rid-mm.md", "Some fact content.", "Changed.",
                           resource_id="m-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_edit_with_resource_id_tombstoned(tools):
    tools.fs_create("uther/test-edit-rid-tomb.md", _card("test-edit-rid-tomb"))
    stat = tools.fs_stat("uther/test-edit-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/test-edit-rid-tomb.md")
    result = tools.fs_edit("uther/test-edit-rid-tomb.md", "Some fact content.", "Changed.",
                           resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── fs_copy ──────────────────────────────────────────────────────────────────

def test_fs_copy_success_envelope(tools):
    tools.fs_create("uther/test-copy-src.md", _card("test-copy-src"))
    result = tools.fs_copy("uther/test-copy-src.md", "uther/test-copy-dst.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-copy-dst.md"
    assert result["resource_id"] is not None
    assert store.ID_RE.fullmatch(result["resource_id"])
    assert result["git"]["committed"] is True


def test_fs_copy_generates_new_id(tools):
    tools.fs_create("uther/test-copy-src2.md", _card("test-copy-src2"))
    src_stat = tools.fs_stat("uther/test-copy-src2.md")
    result = tools.fs_copy("uther/test-copy-src2.md", "uther/test-copy-dst2.md")
    assert result["resource_id"] != src_stat["resource_id"]


def test_fs_copy_source_not_found(tools):
    result = tools.fs_copy("uther/no-src.md", "uther/dst.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_copy_dest_exists(tools):
    tools.fs_create("uther/test-copy-src3.md", _card("test-copy-src3"))
    tools.fs_create("uther/test-copy-dst3.md", _card("test-copy-dst3"))
    result = tools.fs_copy("uther/test-copy-src3.md", "uther/test-copy-dst3.md")
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_copy_updates_name_to_match_dest(tools):
    tools.fs_create("uther/test-copy-src4.md", _card("test-copy-src4"))
    result = tools.fs_copy("uther/test-copy-src4.md", "uther/renamed-copy.md")
    assert result["node_type"] == "file"
    content = tools.fs_read("uther/renamed-copy.md")["content"]
    assert "name: renamed-copy" in content


def test_fs_copy_rejects_cross_tenant_source(tools):
    tools.fs_create("uther/test-copy-src-ct.md", _card("test-copy-src-ct"))
    result = tools.fs_copy("uther/test-copy-src-ct.md", "other-tenant/test-copy-dst.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_rejects_cross_tenant_dest(tools):
    tools.fs_create("uther/test-copy-src-ct2.md", _card("test-copy-src-ct2"))
    result = tools.fs_copy("uther/test-copy-src-ct2.md", "other-tenant/test-copy-dst.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_rejects_cross_tenant_source_only(tools):
    from katana_kernel import GovernedKernel, GovernedVFS, ResourceIdLedger, TransactionManifest
    import os as _os_mod
    repo = tools._repo_root
    mordred_dir = _os_mod.path.join(repo, "mordred")
    if not _os_mod.path.isdir(mordred_dir):
        _os_mod.makedirs(mordred_dir)
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(_os_mod.path.join(repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(_os_mod.path.join(repo, ".katana", "manifests"))
    policy = _memory_policy()
    kernel.bind("memory", policy, vfs, ledger, manifest, repo)
    mordred = FSTools(kernel, "mordred", repo)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_copy("mordred/secret.md", "uther/copied-secret.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_enforces_memory_invariants_on_dest(tools):
    bad = """---
name: bad-source
description: missing fact section
status: active
last_verified: 2026-07-08
metadata:
  type: reference
---

## How to Verify

Run a test.
"""
    tools._vfs.write("uther/bad-source.md", bad, op="fs_create", args={})
    result = tools.fs_copy("uther/bad-source.md", "uther/bad-dest.md")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_copy_with_resource_id_match(tools):
    tools.fs_create("uther/test-copy-rid.md", _card("test-copy-rid"))
    stat = tools.fs_stat("uther/test-copy-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_copy("uther/test-copy-rid.md", "uther/test-copy-rid-dst.md",
                           resource_id=rid)
    assert result["node_type"] == "file"
    assert result["resource_id"] != rid


def test_fs_copy_with_resource_id_mismatch(tools):
    tools.fs_create("uther/test-copy-rid-mm.md", _card("test-copy-rid-mm"))
    result = tools.fs_copy("uther/test-copy-rid-mm.md", "uther/test-copy-rid-mm-dst.md",
                           resource_id="m-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_copy_with_resource_id_tombstoned(tools):
    tools.fs_create("uther/test-copy-rid-tomb.md", _card("test-copy-rid-tomb"))
    stat = tools.fs_stat("uther/test-copy-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/test-copy-rid-tomb.md")
    result = tools.fs_copy("uther/test-copy-rid-tomb.md", "uther/test-copy-rid-tomb-dst.md",
                           resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── fs_rename ────────────────────────────────────────────────────────────────

def test_fs_rename_success_envelope(tools):
    tools.fs_create("uther/test-rename-src.md", _card("test-rename-src"))
    src_stat = tools.fs_stat("uther/test-rename-src.md")
    original_id = src_stat["resource_id"]
    result = tools.fs_rename("uther/test-rename-src.md", "uther/test-rename-dst.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/test-rename-dst.md"
    assert result["resource_id"] == original_id
    assert result["git"]["committed"] is True
    assert tools.fs_stat("uther/test-rename-src.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_rename_preserves_id(tools):
    tools.fs_create("uther/test-rename-id.md", _card("test-rename-id"))
    src_stat = tools.fs_stat("uther/test-rename-id.md")
    original_id = src_stat["resource_id"]
    result = tools.fs_rename("uther/test-rename-id.md", "uther/test-rename-id-dst.md")
    assert result["resource_id"] == original_id
    dst_content = tools.fs_read("uther/test-rename-id-dst.md")["content"]
    assert f"id: {original_id}" in dst_content


def test_fs_rename_source_not_found(tools):
    result = tools.fs_rename("uther/no-src.md", "uther/dst.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_rename_dest_exists(tools):
    tools.fs_create("uther/test-rename-src2.md", _card("test-rename-src2"))
    tools.fs_create("uther/test-rename-dst2.md", _card("test-rename-dst2"))
    result = tools.fs_rename("uther/test-rename-src2.md", "uther/test-rename-dst2.md")
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_rename_updates_name_field(tools):
    tools.fs_create("uther/test-rename-src-name.md", _card("test-rename-src-name"))
    result = tools.fs_rename("uther/test-rename-src-name.md", "uther/renamed-correctly.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "uther/renamed-correctly.md"
    content = tools.fs_read("uther/renamed-correctly.md")["content"]
    assert "name: renamed-correctly" in content


def test_fs_rename_rejects_cross_tenant_source(tools):
    from katana_kernel import GovernedKernel, GovernedVFS, ResourceIdLedger, TransactionManifest
    import os as _os_mod
    repo = tools._repo_root
    mordred_dir = _os_mod.path.join(repo, "mordred")
    if not _os_mod.path.isdir(mordred_dir):
        _os_mod.makedirs(mordred_dir)
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(_os_mod.path.join(repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(_os_mod.path.join(repo, ".katana", "manifests"))
    policy = _memory_policy()
    kernel.bind("memory", policy, vfs, ledger, manifest, repo)
    mordred = FSTools(kernel, "mordred", repo)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_rename("mordred/secret.md", "uther/moved-secret.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_rename_rejects_cross_tenant_dest(tools):
    tools.fs_create("uther/test-rename-src-ct.md", _card("test-rename-src-ct"))
    result = tools.fs_rename("uther/test-rename-src-ct.md", "other-tenant/test-rename-dst.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_rename_with_resource_id_match(tools):
    tools.fs_create("uther/test-rename-rid.md", _card("test-rename-rid"))
    stat = tools.fs_stat("uther/test-rename-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_rename("uther/test-rename-rid.md", "uther/test-rename-rid-dst.md",
                             resource_id=rid)
    assert result["node_type"] == "file"
    assert result["resource_id"] == rid


def test_fs_rename_with_resource_id_mismatch(tools):
    tools.fs_create("uther/test-rename-rid-mm.md", _card("test-rename-rid-mm"))
    result = tools.fs_rename("uther/test-rename-rid-mm.md", "uther/test-rename-rid-mm-dst.md",
                             resource_id="m-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_rename_with_resource_id_tombstoned(tools):
    tools.fs_create("uther/test-rename-rid-tomb.md", _card("test-rename-rid-tomb"))
    stat = tools.fs_stat("uther/test-rename-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/test-rename-rid-tomb.md")
    result = tools.fs_rename("uther/test-rename-rid-tomb.md", "uther/test-rename-rid-tomb-dst.md",
                             resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── fs_delete (tombstone) ────────────────────────────────────────────────────

def test_fs_delete_success_envelope(tools):
    tools.fs_create("uther/test-delete.md", _card("test-delete"))
    stat = tools.fs_stat("uther/test-delete.md")
    rid = stat["resource_id"]
    result = tools.fs_delete("uther/test-delete.md")
    assert result["node_type"] == "file"
    assert result["resource_id"] == rid
    assert result["git"]["committed"] is True
    assert tools.fs_stat("uther/test-delete.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_delete_tombstone_id_not_reused(tools):
    tools.fs_create("uther/test-delete-tomb.md", _card("test-delete-tomb"))
    stat = tools.fs_stat("uther/test-delete-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/test-delete-tomb.md")
    assert tools._binding.ledger.is_tombstoned(rid)
    new = tools.fs_create("uther/test-delete-new.md", _card("test-delete-new"))
    new_rid = new["resource_id"]
    assert new_rid != rid


def test_fs_delete_not_found(tools):
    result = tools.fs_delete("uther/no-such.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_delete_via_mcp(srv):
    mcp, tdir, repo = srv
    create_result = _call(mcp, "fs_create", {
        "path": "uther/test-mcp-delete.md",
        "content": _card("test-mcp-delete"),
    })
    rid = create_result["resource_id"]
    del_result = _call(mcp, "fs_delete", {"path": "uther/test-mcp-delete.md"})
    assert del_result["resource_id"] == rid
    assert del_result["git"]["committed"] is True


def test_fs_delete_with_resource_id_match(tools):
    tools.fs_create("uther/test-delete-rid.md", _card("test-delete-rid"))
    stat = tools.fs_stat("uther/test-delete-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_delete("uther/test-delete-rid.md", resource_id=rid)
    assert result["resource_id"] == rid


def test_fs_delete_with_resource_id_mismatch(tools):
    tools.fs_create("uther/test-delete-rid-mm.md", _card("test-delete-rid-mm"))
    result = tools.fs_delete("uther/test-delete-rid-mm.md", resource_id="m-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_delete_with_resource_id_tombstoned(tools):
    tools.fs_create("uther/test-delete-rid-tomb.md", _card("test-delete-rid-tomb"))
    stat = tools.fs_stat("uther/test-delete-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/test-delete-rid-tomb.md")
    result = tools.fs_delete("uther/test-delete-rid-tomb.md", resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── fs_batch (all-or-nothing + expected_base_commit) ─────────────────────────

def test_fs_batch_success(tools):
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-1.md", "content": _card("batch-1")}},
        {"op": "fs_create", "args": {"path": "uther/batch-2.md", "content": _card("batch-2")}},
    ])
    assert result["node_type"] == "batch"
    assert "batch_results" in result
    assert len(result["batch_results"]) == 2
    assert result["batch_results"][0]["op"] == "fs_create"
    assert result["batch_results"][1]["op"] == "fs_create"
    assert result["git"]["committed"] is True
    assert tools.fs_stat("uther/batch-1.md")["node_type"] == "file"
    assert tools.fs_stat("uther/batch-2.md")["node_type"] == "file"


def test_fs_batch_all_or_nothing(tools):
    tools.fs_create("uther/batch-existing.md", _card("batch-existing"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-new.md", "content": _card("batch-new")}},
        {"op": "fs_create", "args": {"path": "uther/batch-existing.md", "content": _card("batch-existing")}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"
    assert tools.fs_stat("uther/batch-new.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_expected_base_commit_cas(tools):
    tools.fs_create("uther/batch-cas-1.md", _card("batch-cas-1"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("uther/batch-cas-2.md", _card("batch-cas-2"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-cas-3.md", "content": _card("batch-cas-3")}},
    ], expected_base_commit=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


def test_fs_batch_expected_base_commit_success(tools):
    tools.fs_create("uther/batch-commit-1.md", _card("batch-commit-1"))
    from katana_kernel import head_sha
    sha = head_sha(tools._repo_root)
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-commit-2.md", "content": _card("batch-commit-2")}},
    ], expected_base_commit=sha)
    assert result["node_type"] == "batch"


def test_fs_batch_edit(tools):
    tools.fs_create("uther/batch-edit.md", _card("batch-edit"))
    result = tools.fs_batch([
        {"op": "fs_edit", "args": {
            "path": "uther/batch-edit.md",
            "old_string": "Some fact content.",
            "new_string": "Batched edit content.",
        }},
    ])
    assert result["node_type"] == "batch"
    assert "Batched edit content." in tools.fs_read("uther/batch-edit.md")["content"]


def test_fs_batch_copy(tools):
    tools.fs_create("uther/batch-copy-src.md", _card("batch-copy-src"))
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {
            "source": "uther/batch-copy-src.md",
            "dest": "uther/batch-copy-dst.md",
        }},
    ])
    assert result["node_type"] == "batch"
    assert tools.fs_stat("uther/batch-copy-dst.md")["node_type"] == "file"


def test_fs_batch_rename(tools):
    tools.fs_create("uther/batch-rename-src.md", _card("batch-rename-src"))
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {
            "source": "uther/batch-rename-src.md",
            "dest": "uther/batch-rename-dst.md",
        }},
    ])
    assert result["node_type"] == "batch"
    assert tools.fs_stat("uther/batch-rename-dst.md")["node_type"] == "file"
    assert tools.fs_stat("uther/batch-rename-src.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_delete(tools):
    tools.fs_create("uther/batch-del.md", _card("batch-del"))
    stat = tools.fs_stat("uther/batch-del.md")
    rid = stat["resource_id"]
    result = tools.fs_batch([
        {"op": "fs_delete", "args": {"path": "uther/batch-del.md"}},
    ])
    assert result["node_type"] == "batch"
    assert tools.fs_stat("uther/batch-del.md")["code"] == "RESOURCE_NOT_FOUND"
    assert tools._binding.ledger.is_tombstoned(rid)


def test_fs_batch_empty_operations_rejected(tools):
    result = tools.fs_batch([])
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_unknown_op_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_unknown", "args": {}},
    ])
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_via_mcp(srv):
    mcp, tdir, repo = srv
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_create", "args": {"path": "uther/mcp-batch-1.md", "content": _card("mcp-batch-1")}},
            {"op": "fs_create", "args": {"path": "uther/mcp-batch-2.md", "content": _card("mcp-batch-2")}},
        ],
    })
    assert result["node_type"] == "batch"
    assert len(result["batch_results"]) == 2


def test_fs_batch_broken_direct_returns_machine_envelope(tools, monkeypatch):
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["uther/batch-broken.md"]},
    )

    def _raise_broken(*args, **kwargs):
        raise broken

    monkeypatch.setattr(tools._kernel, "mutate", _raise_broken)
    result = tools.fs_batch([
        {"op": "fs_create", "args": {
            "path": "uther/batch-broken.md",
            "content": _card("batch-broken"),
        }},
    ])

    assert result["code"] == result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert result["manual_recovery_required"] is True
    assert "git" not in result


def test_fs_batch_broken_via_mcp_returns_machine_envelope(
    srv, monkeypatch,
):
    mcp, _, _ = srv
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["uther/mcp-batch-broken.md"]},
    )

    def _raise_broken(*args, **kwargs):
        raise broken

    monkeypatch.setattr(
        "katana_kernel.kernel.GovernedKernel.mutate", _raise_broken,
    )
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_create", "args": {
                "path": "uther/mcp-batch-broken.md",
                "content": _card("mcp-batch-broken"),
            }},
        ],
    })

    assert result["code"] == result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert result["manual_recovery_required"] is True
    assert "git" not in result


def test_fs_batch_policy_enforced(tools):
    bad = """---
name: no-fact-card
description: missing fact
status: active
---

## How to Verify

Some verification.
"""
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-policy.md", "content": bad}},
    ])
    assert result["code"] == "INVALID_CONTENT"
    assert tools.fs_stat("uther/batch-policy.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_no_partial_mutation(tools):
    tools.fs_create("uther/batch-partial.md", _card("batch-partial"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-partial-new.md", "content": _card("batch-partial-new")}},
        {"op": "fs_edit", "args": {
            "path": "uther/batch-partial.md",
            "old_string": "nonexistent",
            "new_string": "replacement",
        }},
    ])
    assert result["code"] == "INVALID_CONTENT"
    assert tools.fs_stat("uther/batch-partial-new.md")["code"] == "RESOURCE_NOT_FOUND"


# ── Batch error codes ────────────────────────────────────────────────────────

def test_fs_batch_cross_tenant_path_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "other-tenant/test.md", "content": _card("test")}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_write_cross_tenant_path_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "other-tenant/test.md", "content": _card("test")}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_edit_cross_tenant_path_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_edit", "args": {"path": "other-tenant/test.md", "old_string": "a", "new_string": "b"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_copy_cross_tenant_source_rejected(tools):
    tools.fs_create("uther/batch-copy-src-ct.md", _card("batch-copy-src-ct"))
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "other-tenant/test.md", "dest": "uther/dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_copy_cross_tenant_dest_rejected(tools):
    tools.fs_create("uther/batch-copy-src-ct2.md", _card("batch-copy-src-ct2"))
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "uther/batch-copy-src-ct2.md", "dest": "other-tenant/dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_rename_cross_tenant_source_rejected(tools):
    tools.fs_create("uther/batch-rename-src-ct.md", _card("batch-rename-src-ct"))
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "other-tenant/test.md", "dest": "uther/dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_rename_cross_tenant_dest_rejected(tools):
    tools.fs_create("uther/batch-rename-src-ct2.md", _card("batch-rename-src-ct2"))
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "uther/batch-rename-src-ct2.md", "dest": "other-tenant/dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_delete_cross_tenant_path_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_delete", "args": {"path": "other-tenant/test.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_content_too_large(tools):
    big = "x" * 1_100_000
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-big.md", "content": big}},
    ])
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_fs_batch_write_content_too_large(tools):
    tools.fs_create("uther/batch-write-big.md", _card("batch-write-big"))
    big = "x" * 1_100_000
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "uther/batch-write-big.md", "content": big}},
    ])
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_fs_batch_resource_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "uther/no-such-file.md", "content": _card("test")}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_edit_resource_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_edit", "args": {"path": "uther/no-such-file.md", "old_string": "a", "new_string": "b"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_copy_source_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "uther/no-src.md", "dest": "uther/dst.md"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_rename_source_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "uther/no-src.md", "dest": "uther/dst.md"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_delete_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_delete", "args": {"path": "uther/no-such-file.md"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_resource_exists(tools):
    tools.fs_create("uther/batch-exists.md", _card("batch-exists"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "uther/batch-exists.md", "content": _card("batch-exists")}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_batch_copy_dest_exists(tools):
    tools.fs_create("uther/batch-copy-exists-src.md", _card("batch-copy-exists-src"))
    tools.fs_create("uther/batch-copy-exists-dst.md", _card("batch-copy-exists-dst"))
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "uther/batch-copy-exists-src.md", "dest": "uther/batch-copy-exists-dst.md"}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_batch_rename_dest_exists(tools):
    tools.fs_create("uther/batch-rename-exists-src.md", _card("batch-rename-exists-src"))
    tools.fs_create("uther/batch-rename-exists-dst.md", _card("batch-rename-exists-dst"))
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "uther/batch-rename-exists-src.md", "dest": "uther/batch-rename-exists-dst.md"}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_batch_rename_rejects_no_resource_id(tools):
    content = """---
name: batch-rename-noid
description: no id
status: active
last_verified: 2026-07-08
---

## Fact

Test.

## How to Verify

Test.
"""
    tools._vfs.write("uther/batch-rename-noid-src.md", content, op="fs_create", args={})
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "uther/batch-rename-noid-src.md", "dest": "uther/batch-rename-noid-dst.md"}},
    ])
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_copy_enforces_memory_invariants(tools):
    bad = """---
name: batch-copy-bad
description: missing fact
status: active
last_verified: 2026-07-08
---

## How to Verify

Run a test.
"""
    tools._vfs.write("uther/batch-copy-bad-src.md", bad, op="fs_create", args={})
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "uther/batch-copy-bad-src.md", "dest": "uther/batch-copy-bad-dst.md"}},
    ])
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_resource_id_mismatch(tools):
    tools.fs_create("uther/batch-write-rid-mm.md", _card("batch-write-rid-mm"))
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "uther/batch-write-rid-mm.md", "content": _card("batch-write-rid-mm"), "resource_id": "m-999999"}},
    ])
    assert result["code"] == "REF_MISMATCH"


def test_fs_batch_resource_id_tombstoned(tools):
    tools.fs_create("uther/batch-write-rid-tomb.md", _card("batch-write-rid-tomb"))
    stat = tools.fs_stat("uther/batch-write-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("uther/batch-write-rid-tomb.md")
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "uther/batch-write-rid-tomb.md", "content": _card("batch-write-rid-tomb"), "resource_id": rid}},
    ])
    assert result["code"] == "RESOURCE_REPLACED"


# ── CAS / idempotency conflicts ──────────────────────────────────────────────

def test_fs_create_stale_cas(tools):
    tools.fs_create("uther/cas-1.md", _card("cas-1"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("uther/cas-2.md", _card("cas-2"))
    result = tools.fs_create("uther/cas-3.md", _card("cas-3"),
                             expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


def test_fs_write_stale_cas(tools):
    tools.fs_create("uther/cas-write.md", _card("cas-write"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("uther/cas-write-other.md", _card("cas-write-other"))
    result = tools.fs_write("uther/cas-write.md", _card("cas-write"),
                            expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"


def test_fs_delete_stale_cas(tools):
    tools.fs_create("uther/cas-del.md", _card("cas-del"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("uther/cas-del-other.md", _card("cas-del-other"))
    result = tools.fs_delete("uther/cas-del.md", expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"


def test_fs_create_idempotency_conflict(tools):
    tools.fs_create("uther/idem-1.md", _card("idem-1"),
                    idempotency_key="key-001")
    result = tools.fs_create("uther/idem-2.md", _card("idem-2"),
                             idempotency_key="key-001")
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


def test_fs_write_idempotency_conflict(tools):
    tools.fs_create("uther/idem-write.md", _card("idem-write"),
                    idempotency_key="key-write")
    result = tools.fs_write("uther/idem-write.md", _card("idem-write"),
                            idempotency_key="key-write")
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


# ── REF_MISMATCH / RESOURCE_REPLACED ─────────────────────────────────────────

def test_fs_resolve_ref_mismatch(tools):
    tools.fs_create("uther/ref-match.md", _card("ref-match"))
    stat = tools.fs_stat("uther/ref-match.md")
    rid = stat["resource_id"]
    result = tools.fs_resolve(rid)
    assert result["resource_id"] == rid
    assert result["virtual_path"] == "uther/ref-match.md"


def test_fs_write_id_mismatch(tools):
    tools.fs_create("uther/write-mismatch.md", _card("write-mismatch"))
    modified = _card("write-mismatch").replace("name: write-mismatch", "name: write-mismatch\nid: m-999999")
    result = tools.fs_write("uther/write-mismatch.md", modified)
    assert result["code"] == "REF_MISMATCH"


# ── Path traversal / cross-domain write rejection ───────────────────────────

def test_fs_create_path_traversal_rejected(tools):
    result = tools.fs_create("../escape.md", _card("escape"))
    assert result["code"] == "INVALID_PATH"


def test_fs_read_path_traversal_rejected(tools):
    result = tools.fs_read("../etc/passwd")
    assert result["code"] == "INVALID_PATH"


def test_fs_write_path_traversal_rejected(tools):
    result = tools.fs_write("../etc/passwd", _card("passwd"))
    assert result["code"] == "INVALID_PATH"


# ── Error envelope field presence ────────────────────────────────────────────

def test_error_envelope_has_code_and_message(tools):
    result = tools.fs_stat("no/such/file.md")
    assert "code" in result
    assert "message" in result


def test_error_envelope_has_retryable(tools):
    result = tools.fs_stat("no/such/file.md")
    assert "retryable" in result
    assert isinstance(result["retryable"], bool)


def test_error_envelope_has_current_commit(tools):
    result = tools.fs_stat("no/such/file.md")
    assert "current_commit" in result


def test_conflict_error_envelope_has_expected_revision(tools):
    tools.fs_create("uther/conflict-1.md", _card("conflict-1"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("uther/conflict-2.md", _card("conflict-2"))
    result = tools.fs_create("uther/conflict-3.md", _card("conflict-3"),
                             expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert "expected_revision" in result


# ── Memory hard invariants on fs_* write paths ───────────────────────────────

def test_fs_create_rejects_no_fact_section(tools):
    bad = """---
name: no-fact-card
description: missing fact
status: active
---

## How to Verify

Some verification.
"""
    result = tools.fs_create("uther/no-fact-card.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_rejects_no_verify_section(tools):
    bad = """---
name: no-verify-card
description: missing verify
status: active
---

## Fact

Some fact.
"""
    result = tools.fs_create("uther/no-verify-card.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_write_rejects_invalid_card(tools):
    tools.fs_create("uther/write-invalid.md", _card("write-invalid"))
    bad = "not a valid card at all"
    result = tools.fs_write("uther/write-invalid.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_rejects_unparseable_result(tools):
    tools.fs_create("uther/edit-parse.md", _card("edit-parse"))
    result = tools.fs_edit("uther/edit-parse.md",
                           "---", "---\nbroken: [")
    assert result["code"] == "INVALID_CONTENT"


# ── No partial mutation on error ─────────────────────────────────────────────

def test_fs_edit_no_partial_mutation(tools):
    tools.fs_create("uther/no-partial.md", _card("no-partial"))
    original = tools.fs_read("uther/no-partial.md")["content"]
    result = tools.fs_edit("uther/no-partial.md", "nonexistent", "replacement")
    assert result["code"] == "INVALID_CONTENT"
    after = tools.fs_read("uther/no-partial.md")["content"]
    assert after == original


def test_fs_write_no_partial_mutation(tools):
    tools.fs_create("uther/no-partial-write.md", _card("no-partial-write"))
    original = tools.fs_read("uther/no-partial-write.md")["content"]
    bad = _card("no-partial-write").replace("name: no-partial-write", "name: no-partial-write\nid: m-999999")
    result = tools.fs_write("uther/no-partial-write.md", bad)
    assert result["code"] == "REF_MISMATCH"
    after = tools.fs_read("uther/no-partial-write.md")["content"]
    assert "id: m-999999" not in after


# ── R3: status / type / last_verified field validation ──────────────────────

def _card_with_status(name, status):
    return f"""---
name: {name}
description: Test card
status: {status}
last_verified: 2026-07-08
metadata:
  type: reference
---

## Fact

Some fact.

## How to Verify

Run a test.
"""


def _card_with_type(name, ctype):
    return f"""---
name: {name}
description: Test card
status: active
last_verified: 2026-07-08
metadata:
  type: {ctype}
---

## Fact

Some fact.

## How to Verify

Run a test.
"""


def test_fs_create_rejects_invalid_status(tools):
    result = tools.fs_create("uther/bad-status.md", _card_with_status("bad-status", "bogus"))
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_rejects_invalid_type(tools):
    result = tools.fs_create("uther/bad-type.md", _card_with_type("bad-type", "nonsense"))
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_rejects_multiline_description(tools):
    bad = """---
name: bad-desc
description: |
  multi
  line
status: active
last_verified: 2026-07-08
metadata:
  type: reference
---

## Fact

Some fact.

## How to Verify

Run a test.
"""
    result = tools.fs_create("uther/bad-desc.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_write_rejects_invalid_status(tools):
    tools.fs_create("uther/write-status.md", _card("write-status"))
    result = tools.fs_write("uther/write-status.md", _card_with_status("write-status", "bogus"))
    assert result["code"] == "INVALID_CONTENT"


def test_fs_write_rejects_invalid_type(tools):
    tools.fs_create("uther/write-type.md", _card("write-type"))
    result = tools.fs_write("uther/write-type.md", _card_with_type("write-type", "nonsense"))
    assert result["code"] == "INVALID_CONTENT"


# ── R4: fs_edit section removal rejection ──────────────────────────────────

def test_fs_edit_rejects_fact_section_removal(tools):
    tools.fs_create("uther/edit-remove-fact.md", _card("edit-remove-fact"))
    result = tools.fs_edit("uther/edit-remove-fact.md",
                           "## Fact", "## Facts")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_rejects_verify_section_removal(tools):
    tools.fs_create("uther/edit-remove-verify.md", _card("edit-remove-verify"))
    result = tools.fs_edit("uther/edit-remove-verify.md",
                           "## How to Verify", "## Verify")
    assert result["code"] == "INVALID_CONTENT"


# ── R5: policy enforcement via kernel.mutate ───────────────────────────────

def test_policy_rejects_fs_create_missing_fact_at_kernel_layer(tools):
    bad = """---
name: policy-no-fact
description: missing fact
status: active
last_verified: 2026-07-08
metadata:
  type: reference
---

## How to Verify

Some verification.
"""
    result = tools.fs_create("uther/policy-no-fact.md", bad)
    assert result["code"] == "INVALID_CONTENT"
    assert "Fact" in result["message"]


# ── N3: tenant scoping ─────────────────────────────────────────────────────

def test_fs_create_rejects_cross_tenant_path(tools):
    result = tools.fs_create("other-tenant/some.md", _card("cross-tenant"))
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_rejects_cross_tenant_dest(tools):
    tools.fs_create("uther/cross-copy-src.md", _card("cross-copy-src"))
    result = tools.fs_copy("uther/cross-copy-src.md", "other-tenant/cross-copy-dst.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_rename_rejects_cross_tenant_dest(tools):
    tools.fs_create("uther/cross-rename-src.md", _card("cross-rename-src"))
    result = tools.fs_rename("uther/cross-rename-src.md", "other-tenant/cross-rename-dst.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_create_rejects_root_path(tools):
    result = tools.fs_create("root-file.md", _card("root-file"))
    assert result["code"] == "INVALID_PATH"


# ── R1: Cross-tenant read leak prevention ──────────────────────────────────

def _make_mordred_tools(tools_obj):
    from katana_kernel import (
        GovernedKernel,
        GovernedVFS,
        ResourceIdLedger,
        TransactionManifest,
    )
    import os as _os
    repo = tools_obj._repo_root
    mordred_dir = _os.path.join(repo, "mordred")
    if not _os.path.isdir(mordred_dir):
        _os.makedirs(mordred_dir)
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(_os.path.join(repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(_os.path.join(repo, ".katana", "manifests"))
    policy = _memory_policy()
    kernel.bind("memory", policy, vfs, ledger, manifest, repo)
    return FSTools(kernel, "mordred", repo)


def test_fs_read_rejects_cross_tenant_path(tools):
    mordred = _make_mordred_tools(tools)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_read("mordred/secret.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_stat_rejects_cross_tenant_path(tools):
    mordred = _make_mordred_tools(tools)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_stat("mordred/secret.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_resolve_rejects_cross_tenant_path(tools):
    mordred = _make_mordred_tools(tools)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_resolve("mordred/secret.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_list_rejects_cross_tenant_path(tools):
    mordred = _make_mordred_tools(tools)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_list("mordred")
    assert result["code"] == "INVALID_PATH"


def test_fs_list_empty_constrained_to_tenant(tools):
    mordred = _make_mordred_tools(tools)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    tools.fs_create("uther/visible.md", _card("visible"))
    result = tools.fs_list("")
    assert result["node_type"] == "directory"
    paths = [e["virtual_path"] for e in result["entries"]]
    assert "uther/visible.md" in paths
    assert "mordred/secret.md" not in paths


def test_fs_glob_rejects_cross_tenant_pattern(tools):
    mordred = _make_mordred_tools(tools)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_glob("mordred/*.md")
    assert result["code"] == "INVALID_PATH"


# ── R2: fs_glob traversal / ejection prevention ────────────────────────────

def test_fs_glob_rejects_traversal_dotdot(tools):
    result = tools.fs_glob("../*.md")
    assert result["code"] == "INVALID_PATH"
    assert "retryable" in result


def test_fs_glob_rejects_absolute_path(tools):
    result = tools.fs_glob("/etc/passwd")
    assert result["code"] == "INVALID_PATH"


def test_fs_glob_rejects_cross_tenant_traversal(tools):
    mordred = _make_mordred_tools(tools)
    mordred.fs_create("mordred/secret.md", _card("secret"))
    result = tools.fs_glob("mordred/../*.md")
    assert result["code"] == "INVALID_PATH"


# ── R3: MCP-transport CAS revision / idempotency conflict tests ────────────

def test_fs_write_revision_conflict_via_mcp(srv):
    mcp, tdir, repo = srv
    create = _call(mcp, "fs_create", {
        "path": "uther/rev-mcp.md",
        "content": _card("rev-mcp"),
    })
    old_rev = create["resource_revision"]
    _call(mcp, "fs_write", {
        "path": "uther/rev-mcp.md",
        "content": _card("rev-mcp", body_fact="First update."),
    })
    result = _call(mcp, "fs_write", {
        "path": "uther/rev-mcp.md",
        "content": _card("rev-mcp"),
        "expected_resource_revision": old_rev,
    })
    assert result["code"] == "REVISION_CONFLICT"
    assert result["retryable"] is True


def test_fs_create_idempotency_conflict_via_mcp(srv):
    mcp, tdir, repo = srv
    _call(mcp, "fs_create", {
        "path": "uther/idem-mcp-1.md",
        "content": _card("idem-mcp-1"),
        "idempotency_key": "mcp-key-001",
    })
    result = _call(mcp, "fs_create", {
        "path": "uther/idem-mcp-2.md",
        "content": _card("idem-mcp-2"),
        "idempotency_key": "mcp-key-001",
    })
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


def test_fs_write_idempotency_conflict_via_mcp(srv):
    mcp, tdir, repo = srv
    create = _call(mcp, "fs_create", {
        "path": "uther/idem-write-mcp.md",
        "content": _card("idem-write-mcp"),
        "idempotency_key": "mcp-key-write",
    })
    result = _call(mcp, "fs_write", {
        "path": "uther/idem-write-mcp.md",
        "content": _card("idem-write-mcp", body_fact="Updated."),
        "idempotency_key": "mcp-key-write",
    })
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


def test_fs_edit_idempotency_conflict_via_mcp(srv):
    mcp, tdir, repo = srv
    _call(mcp, "fs_create", {
        "path": "uther/idem-edit-mcp.md",
        "content": _card("idem-edit-mcp"),
        "idempotency_key": "mcp-key-edit",
    })
    result = _call(mcp, "fs_edit", {
        "path": "uther/idem-edit-mcp.md",
        "old_string": "Some fact content.",
        "new_string": "Edited.",
        "idempotency_key": "mcp-key-edit",
    })
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


def test_fs_delete_idempotency_conflict_via_mcp(srv):
    mcp, tdir, repo = srv
    _call(mcp, "fs_create", {
        "path": "uther/idem-del-mcp.md",
        "content": _card("idem-del-mcp"),
        "idempotency_key": "mcp-key-del",
    })
    result = _call(mcp, "fs_delete", {
        "path": "uther/idem-del-mcp.md",
        "idempotency_key": "mcp-key-del",
    })
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


def test_fs_batch_idempotency_conflict_via_mcp(srv):
    mcp, tdir, repo = srv
    _call(mcp, "fs_create", {
        "path": "uther/idem-batch-mcp-1.md",
        "content": _card("idem-batch-mcp-1"),
        "idempotency_key": "mcp-key-batch",
    })
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_create", "args": {"path": "uther/idem-batch-mcp-2.md", "content": _card("idem-batch-mcp-2")}},
        ],
        "idempotency_key": "mcp-key-batch",
    })
    assert result["code"] == "IDEMPOTENCY_CONFLICT"
