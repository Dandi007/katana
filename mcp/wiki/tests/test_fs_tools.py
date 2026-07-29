"""fs_* Full VFS contract tests for Wiki app.

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
from katana_wiki_mcp import server as _server_mod
from katana_wiki_mcp.fs_tools import FSTools, ID_RE

WIKI_PAGE_CONTENT = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
source_type: human
credibility: high
sources:
  - 测试来源
摘要: 测试页面
---

正文内容，包含 [[测试链接]]。

# References

- 测试参考
"""

WIKI_PAGE_TWO = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
source_type: human
credibility: high
sources:
  - 测试来源二
摘要: 第二个测试页面
---

另一个页面的正文，包含 [[另一个链接]]。

# References

- 另一个参考
"""


def _page(title="测试页面", body_text="正文内容，包含 [[测试链接]]。", refs="- 测试参考"):
    return f"""---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
source_type: human
credibility: high
sources:
  - 测试来源
摘要: {title[:10]}
---

{body_text}

# References

{refs}
"""


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return str(tmp_path)


def _call(mcp, tool, args=None):
    async def go():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args or {})).data
    return asyncio.run(go())


def _wiki_policy():
    from katana_kernel import DomainPolicy
    from katana_wiki_mcp import invariants as _inv
    from katana_wiki_mcp.pages import parse_page

    def _invariants(domain, op, args):
        if op.startswith("fs_") and op not in ("fs_batch", "fs_capabilities", "fs_resolve",
                                                  "fs_stat", "fs_list", "fs_glob", "fs_read"):
            content = args.get("content")
            if content is not None:
                fm, body = parse_page(content)
                errs = _inv.validate_page(fm, body, require_summary=True, require_sources=True)
                if errs:
                    raise ValueError("; ".join(errs))

    return DomainPolicy(
        domain="wiki",
        allowed_ops={
            "delete",
            "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename",
            "fs_delete", "fs_batch",
        },
        invariants=[_invariants],
    )


@pytest.fixture
def srv(tmp_path):
    repo = _init_repo(tmp_path)
    from katana_kernel import (
        GovernedKernel,
        GovernedVFS,
        ResourceIdLedger,
        TransactionManifest,
    )
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json"), prefix="w-")
    manifest = TransactionManifest(os.path.join(repo, ".katana", "manifests"))
    policy = _wiki_policy()
    kernel.bind("wiki", policy, vfs, ledger, manifest, repo)
    fs_tools = FSTools(kernel, repo)
    mcp = _server_mod.mcp
    _server_mod._kernel = kernel
    _server_mod._fs_tools = fs_tools
    return mcp, repo, fs_tools


@pytest.fixture
def tools(tmp_path):
    repo = _init_repo(tmp_path)
    from katana_kernel import (
        GovernedKernel,
        GovernedVFS,
        ResourceIdLedger,
        TransactionManifest,
    )
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json"), prefix="w-")
    manifest = TransactionManifest(os.path.join(repo, ".katana", "manifests"))
    policy = _wiki_policy()
    kernel.bind("wiki", policy, vfs, ledger, manifest, repo)
    return FSTools(kernel, repo)


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
    mcp, repo, tools = srv
    result = _call(mcp, "fs_capabilities")
    assert "capabilities" in result
    assert "fs_read" in result["capabilities"]["operations"]


def test_fs_create_broken_is_machine_readable_and_never_success(
    tools, monkeypatch,
):
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["broken.md"]},
    )

    def _raise_broken(*args, **kwargs):
        raise broken

    monkeypatch.setattr(tools._kernel, "mutate", _raise_broken)
    result = tools.fs_create("broken.md", _page("broken"))

    assert result["code"] == "BROKEN"
    assert result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert result["manual_recovery_required"] is True
    assert "git" not in result


def test_wiki_server_broken_envelope_is_not_success():
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["broken.md"]},
    )
    result = _server_mod._server_mutation(
        lambda: (_ for _ in ()).throw(broken)
    )

    assert result["code"] == result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert "git" not in result


# ── fs_resolve ───────────────────────────────────────────────────────────────

def test_fs_resolve_by_path(tools):
    tools.fs_create("test-resolve.md", _page("test-resolve"))
    result = tools.fs_resolve("test-resolve.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-resolve.md"
    assert result["resource_id"] is not None
    assert ID_RE.fullmatch(result["resource_id"])
    assert result["content_hash"] is not None
    assert result["content_hash"].startswith("sha256:")
    assert result["resource_revision"] is not None
    assert result["content_revision"] is not None
    assert result["commit"] is not None


def test_fs_resolve_by_id(tools):
    tools.fs_create("test-resolve-id.md", _page("test-resolve-id"))
    stat = tools.fs_stat("test-resolve-id.md")
    rid = stat["resource_id"]
    result = tools.fs_resolve(rid)
    assert result["resource_id"] == rid
    assert result["virtual_path"] == "test-resolve-id.md"


def test_fs_resolve_not_found(tools):
    result = tools.fs_resolve("nonexistent.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_resolve_bad_id(tools):
    result = tools.fs_resolve("w-ffffff")
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert result["resource_id"] == "w-ffffff"


def test_fs_resolve_tombstoned_returns_replaced(tools):
    tools.fs_create("tomb-resolve.md", _page("tomb-resolve"))
    stat = tools.fs_stat("tomb-resolve.md")
    rid = stat["resource_id"]
    tools.fs_delete("tomb-resolve.md")
    result = tools.fs_resolve(rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_resolve_path_traversal_rejected(tools):
    result = tools.fs_resolve("../etc/passwd")
    assert result["code"] == "INVALID_PATH"


# ── fs_stat ──────────────────────────────────────────────────────────────────

def test_fs_stat_file_success_envelope(tools):
    tools.fs_create("test-stat.md", _page("test-stat"))
    result = tools.fs_stat("test-stat.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-stat.md"
    assert result["resource_id"] is not None
    assert ID_RE.fullmatch(result["resource_id"])
    assert result["size"] > 0
    assert result["media_type"] == "text/markdown"
    assert result["content_hash"] is not None
    assert result["content_hash"].startswith("sha256:")
    assert result["resource_revision"] is not None
    assert result["content_revision"] is not None
    assert result["commit"] is not None


def test_fs_stat_directory(tools):
    os.makedirs(os.path.join(tools._repo_root, "testdir"))
    result = tools.fs_stat("testdir")
    assert result["node_type"] == "directory"
    assert result["resource_id"] is None
    assert result["size"] is None
    assert result["media_type"] is None


def test_fs_stat_not_found(tools):
    result = tools.fs_stat("nope.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_stat_path_traversal_rejected(tools):
    result = tools.fs_stat("../etc/passwd")
    assert result["code"] == "INVALID_PATH"


# ── fs_list ──────────────────────────────────────────────────────────────────

def test_fs_list_root(tools):
    tools.fs_create("test-list-1.md", _page("test-list-1"))
    tools.fs_create("test-list-2.md", _page("test-list-2"))
    result = tools.fs_list("")
    assert result["node_type"] == "directory"
    assert "entries" in result
    paths = [e["virtual_path"] for e in result["entries"]]
    assert "test-list-1.md" in paths
    assert "test-list-2.md" in paths
    for entry in result["entries"]:
        assert entry["node_type"] == "file"
        assert entry["resource_id"] is not None
        assert entry["size"] > 0
        assert entry["media_type"] == "text/markdown"
        assert entry["content_hash"] is not None


def test_fs_list_directory(tools):
    os.makedirs(os.path.join(tools._repo_root, "listdir"))
    tools.fs_create("listdir/test-list-1.md", _page("test-list-1"))
    tools.fs_create("listdir/test-list-2.md", _page("test-list-2"))
    result = tools.fs_list("listdir")
    assert result["node_type"] == "directory"
    assert "entries" in result
    paths = [e["virtual_path"] for e in result["entries"]]
    assert "listdir/test-list-1.md" in paths
    assert "listdir/test-list-2.md" in paths


def test_fs_list_empty_root(tools):
    result = tools.fs_list("")
    assert result["node_type"] == "directory"
    assert "entries" in result


def test_fs_list_not_directory(tools):
    tools.fs_create("test-list-file.md", _page("test-list-file"))
    result = tools.fs_list("test-list-file.md")
    assert result["code"] == "INVALID_PATH"


# ── fs_glob ──────────────────────────────────────────────────────────────────

def test_fs_glob_pattern(tools):
    tools.fs_create("test-glob-a.md", _page("test-glob-a"))
    tools.fs_create("test-glob-b.md", _page("test-glob-b"))
    tools.fs_create("other-glob.md", _page("other-glob"))
    result = tools.fs_glob("test-glob-*.md")
    assert "hits" in result
    assert len(result["hits"]) == 2
    assert "entries" in result
    assert len(result["entries"]) == 2


def test_fs_glob_no_match(tools):
    result = tools.fs_glob("nonexistent-*.md")
    assert result["hits"] == []
    assert result["entries"] == []


def test_fs_glob_traversal_rejected(tools):
    result = tools.fs_glob("../*")
    assert result["code"] == "INVALID_PATH"


# ── fs_read ──────────────────────────────────────────────────────────────────

def test_fs_read_success_envelope(tools):
    tools.fs_create("test-read.md", _page("test-read"))
    result = tools.fs_read("test-read.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-read.md"
    assert result["resource_id"] is not None
    assert result["size"] > 0
    assert result["content_hash"] is not None
    assert "content" in result
    assert "正文内容" in result["content"]
    assert "total_lines" in result
    assert result["total_lines"] > 0


def test_fs_read_not_found(tools):
    result = tools.fs_read("nope.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_read_offset_limit(tools):
    tools.fs_create("test-read-ol.md", _page("test-read-ol"))
    result = tools.fs_read("test-read-ol.md", offset=1, limit=3)
    assert result["offset"] == 1
    assert result["limit"] == 3


def test_fs_read_path_traversal_rejected(tools):
    result = tools.fs_read("../etc/passwd")
    assert result["code"] == "INVALID_PATH"


# ── fs_create ────────────────────────────────────────────────────────────────

def test_fs_create_success_envelope(tools):
    result = tools.fs_create("test-create.md", _page("test-create"))
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-create.md"
    assert result["resource_id"] is not None
    assert ID_RE.fullmatch(result["resource_id"])
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
    c1 = tools.fs_create("test-create-id-1.md", _page("test-create-id-1"))
    c2 = tools.fs_create("test-create-id-2.md", _page("test-create-id-2"))
    assert c1["resource_id"] != c2["resource_id"]
    assert ID_RE.fullmatch(c1["resource_id"])
    assert ID_RE.fullmatch(c2["resource_id"])


def test_fs_create_injects_id_into_content(tools):
    result = tools.fs_create("test-create-content.md", _page("test-create-content"))
    rid = result["resource_id"]
    content = tools.fs_read("test-create-content.md")["content"]
    assert f"id: {rid}" in content


def test_fs_create_duplicate_path_rejected(tools):
    tools.fs_create("test-create-dup.md", _page("test-create-dup"))
    result = tools.fs_create("test-create-dup.md", _page("test-create-dup"))
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_create_invalid_content_rejected(tools):
    result = tools.fs_create("test-create-bad.md", "not valid page content")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_missing_frontmatter_rejected(tools):
    bad = "just some text without frontmatter"
    result = tools.fs_create("bad.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_content_too_large(tools):
    big = "x" * 1_100_000
    result = tools.fs_create("big.md", big)
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_fs_create_with_explicit_resource_id(tools):
    result = tools.fs_create("test-create-explicit-id.md", _page("test-create-explicit-id"),
                             resource_id="w-abc123")
    assert result["resource_id"] == "w-abc123"
    content = tools.fs_read("test-create-explicit-id.md")["content"]
    assert "id: w-abc123" in content


def test_fs_create_explicit_id_already_exists(tools):
    tools.fs_create("test-create-dup-id.md", _page("test-create-dup-id"),
                    resource_id="w-abc456")
    result = tools.fs_create("test-create-dup-id-2.md", _page("test-create-dup-id-2"),
                             resource_id="w-abc456")
    assert result["code"] == "REF_MISMATCH"


def test_fs_create_explicit_id_tombstoned(tools):
    tools.fs_create("tomb-create-id.md", _page("tomb-create-id"))
    stat = tools.fs_stat("tomb-create-id.md")
    rid = stat["resource_id"]
    tools.fs_delete("tomb-create-id.md")
    result = tools.fs_create("tomb-create-id-2.md", _page("tomb-create-id-2"),
                             resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_create_via_mcp(srv):
    mcp, repo, tools = srv
    result = _call(mcp, "fs_create", {
        "path": "test-mcp-create.md",
        "content": _page("test-mcp-create"),
    })
    assert result["resource_id"] is not None
    assert ID_RE.fullmatch(result["resource_id"])


def test_fs_create_path_traversal_rejected(tools):
    result = tools.fs_create("../escape.md", _page("escape"))
    assert result["code"] == "INVALID_PATH"


# ── fs_write (no implicit create) ────────────────────────────────────────────

def test_fs_write_success_envelope(tools):
    tools.fs_create("test-write.md", _page("test-write"))
    modified = _page("test-write", body_text="更新后的正文，包含 [[链接]]。")
    result = tools.fs_write("test-write.md", modified)
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-write.md"
    assert result["resource_id"] is not None
    assert result["size"] > 0
    assert "更新后的正文" in result["content"]
    assert result["git"]["committed"] is True


def test_fs_write_no_implicit_create(tools):
    result = tools.fs_write("no-such-file.md", _page("no-such-file"))
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert "does not implicitly create" in result["message"]


def test_fs_write_id_immutable(tools):
    tools.fs_create("test-write-id.md", _page("test-write-id"))
    modified = _page("test-write-id").replace("摘要: test-write", "id: w-999999\n摘要: test-write")
    result = tools.fs_write("test-write-id.md", modified)
    assert result["code"] == "REF_MISMATCH"


def test_fs_write_invalid_content_rejected(tools):
    tools.fs_create("test-write-bad.md", _page("test-write-bad"))
    result = tools.fs_write("test-write-bad.md", "not valid")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_write_revision_conflict(tools):
    tools.fs_create("rev-conflict.md", _page("rev-conflict"))
    stat = tools.fs_stat("rev-conflict.md")
    old_rev = stat["resource_revision"]
    modified = _page("rev-conflict", body_text="First update. [[链接]]")
    tools.fs_write("rev-conflict.md", modified)
    result = tools.fs_write("rev-conflict.md", _page("rev-conflict"),
                            expected_resource_revision=old_rev)
    assert result["code"] == "REVISION_CONFLICT"
    assert result["retryable"] is True


def test_fs_write_resource_replaced_after_delete(tools):
    tools.fs_create("repl-write.md", _page("repl-write"))
    stat = tools.fs_stat("repl-write.md")
    rid = stat["resource_id"]
    tools.fs_delete("repl-write.md")
    result = tools.fs_write("repl-write.md", _page("repl-write"))
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_write_with_resource_id_match(tools):
    tools.fs_create("test-write-rid.md", _page("test-write-rid"))
    stat = tools.fs_stat("test-write-rid.md")
    rid = stat["resource_id"]
    modified = _page("test-write-rid", body_text="Updated with rid. [[链接]]")
    result = tools.fs_write("test-write-rid.md", modified, resource_id=rid)
    assert result["node_type"] == "file"


def test_fs_write_with_resource_id_mismatch(tools):
    tools.fs_create("test-write-rid-mm.md", _page("test-write-rid-mm"))
    modified = _page("test-write-rid-mm", body_text="Updated. [[链接]]")
    result = tools.fs_write("test-write-rid-mm.md", modified, resource_id="w-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_write_with_resource_id_tombstoned(tools):
    tools.fs_create("test-write-rid-tomb.md", _page("test-write-rid-tomb"))
    stat = tools.fs_stat("test-write-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-write-rid-tomb.md")
    result = tools.fs_write("test-write-rid-tomb.md", _page("test-write-rid-tomb"),
                            resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_write_path_traversal_rejected(tools):
    result = tools.fs_write("../etc/passwd", _page("passwd"))
    assert result["code"] == "INVALID_PATH"


# ── fs_edit (exact-match) ────────────────────────────────────────────────────

def test_fs_edit_success_envelope(tools):
    tools.fs_create("test-edit.md", _page("test-edit"))
    result = tools.fs_edit("test-edit.md", "正文内容", "编辑后的正文")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-edit.md"
    assert "编辑后的正文" in result["content"]
    assert result["git"]["committed"] is True


def test_fs_edit_exact_match_required(tools):
    tools.fs_create("test-edit-exact.md", _page("test-edit-exact"))
    result = tools.fs_edit("test-edit-exact.md", "nonexistent string", "replacement")
    assert result["code"] == "INVALID_CONTENT"
    assert "not found" in result["message"]


def test_fs_edit_non_unique_requires_replace_all(tools):
    content = _page("edit-dup", body_text="Here is some text. Here is more. [[链接]]")
    tools.fs_create("edit-dup.md", content)
    result = tools.fs_edit("edit-dup.md", "Here is", "There is")
    assert result["code"] == "INVALID_CONTENT"
    assert "matches" in result["message"]

    result2 = tools.fs_edit("edit-dup.md", "Here is", "There is", replace_all=True)
    assert "There is" in result2["content"]
    assert "Here is" not in result2["content"]


def test_fs_edit_id_immutable(tools):
    tools.fs_create("test-edit-id.md", _page("test-edit-id"))
    stat = tools.fs_stat("test-edit-id.md")
    original_id = stat["resource_id"]
    result = tools.fs_edit("test-edit-id.md",
                           f"id: {original_id}", "id: w-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_edit_old_string_empty_rejected(tools):
    tools.fs_create("test-edit-empty.md", _page("test-edit-empty"))
    result = tools.fs_edit("test-edit-empty.md", "", "x")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_noop_rejected(tools):
    tools.fs_create("test-edit-noop.md", _page("test-edit-noop"))
    result = tools.fs_edit("test-edit-noop.md", "正文内容", "正文内容")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_revision_conflict(tools):
    tools.fs_create("edit-rev.md", _page("edit-rev"))
    stat = tools.fs_stat("edit-rev.md")
    old_rev = stat["resource_revision"]
    tools.fs_edit("edit-rev.md", "正文内容", "First edit.")
    result = tools.fs_edit("edit-rev.md", "First edit.", "Second edit.",
                           expected_resource_revision=old_rev)
    assert result["code"] == "REVISION_CONFLICT"


def test_fs_edit_with_resource_id_match(tools):
    tools.fs_create("test-edit-rid.md", _page("test-edit-rid"))
    stat = tools.fs_stat("test-edit-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_edit("test-edit-rid.md", "正文内容", "RID edited.",
                           resource_id=rid)
    assert result["node_type"] == "file"


def test_fs_edit_with_resource_id_mismatch(tools):
    tools.fs_create("test-edit-rid-mm.md", _page("test-edit-rid-mm"))
    result = tools.fs_edit("test-edit-rid-mm.md", "正文内容", "Changed.",
                           resource_id="w-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_edit_with_resource_id_tombstoned(tools):
    tools.fs_create("test-edit-rid-tomb.md", _page("test-edit-rid-tomb"))
    stat = tools.fs_stat("test-edit-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-edit-rid-tomb.md")
    result = tools.fs_edit("test-edit-rid-tomb.md", "正文内容", "Changed.",
                           resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_edit_path_traversal_rejected(tools):
    tools.fs_create("test-edit-pt.md", _page("test-edit-pt"))
    result = tools.fs_edit("../etc/passwd", "a", "b")
    assert result["code"] == "INVALID_PATH"


# ── fs_copy ──────────────────────────────────────────────────────────────────

def test_fs_copy_success_envelope(tools):
    tools.fs_create("test-copy-src.md", _page("test-copy-src"))
    result = tools.fs_copy("test-copy-src.md", "test-copy-dst.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-copy-dst.md"
    assert result["resource_id"] is not None
    assert ID_RE.fullmatch(result["resource_id"])
    assert result["git"]["committed"] is True


def test_fs_copy_generates_new_id(tools):
    tools.fs_create("test-copy-src2.md", _page("test-copy-src2"))
    src_stat = tools.fs_stat("test-copy-src2.md")
    result = tools.fs_copy("test-copy-src2.md", "test-copy-dst2.md")
    assert result["resource_id"] != src_stat["resource_id"]


def test_fs_copy_source_not_found(tools):
    result = tools.fs_copy("no-src.md", "dst.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_copy_dest_exists(tools):
    tools.fs_create("test-copy-src3.md", _page("test-copy-src3"))
    tools.fs_create("test-copy-dst3.md", _page("test-copy-dst3"))
    result = tools.fs_copy("test-copy-src3.md", "test-copy-dst3.md")
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_copy_enforces_wiki_invariants_on_dest(tools):
    bad = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
---

无出链无来源无法验证。
"""
    tools._vfs.write("bad-source.md", bad, op="fs_create", args={})
    result = tools.fs_copy("bad-source.md", "bad-dest.md")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_copy_with_resource_id_match(tools):
    tools.fs_create("test-copy-rid.md", _page("test-copy-rid"))
    stat = tools.fs_stat("test-copy-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_copy("test-copy-rid.md", "test-copy-rid-dst.md",
                           resource_id=rid)
    assert result["node_type"] == "file"
    assert result["resource_id"] != rid


def test_fs_copy_with_resource_id_mismatch(tools):
    tools.fs_create("test-copy-rid-mm.md", _page("test-copy-rid-mm"))
    result = tools.fs_copy("test-copy-rid-mm.md", "test-copy-rid-mm-dst.md",
                           resource_id="w-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_copy_with_resource_id_tombstoned(tools):
    tools.fs_create("test-copy-rid-tomb.md", _page("test-copy-rid-tomb"))
    stat = tools.fs_stat("test-copy-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-copy-rid-tomb.md")
    result = tools.fs_copy("test-copy-rid-tomb.md", "test-copy-rid-tomb-dst.md",
                           resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_copy_path_traversal_source_rejected(tools):
    result = tools.fs_copy("../etc/passwd", "dst.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_path_traversal_dest_rejected(tools):
    tools.fs_create("src.md", _page("src"))
    result = tools.fs_copy("src.md", "../etc/passwd")
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_cross_domain_source_rejected(tools):
    os.makedirs(os.path.join(tools._repo_root, ".git"), exist_ok=True)
    with open(os.path.join(tools._repo_root, ".git", "test.md"), "w") as f:
        f.write(_page("secret"))
    result = tools.fs_copy(".git/test.md", "dst.md")
    assert result["code"] == "INVALID_PATH"


# ── fs_rename ────────────────────────────────────────────────────────────────

def test_fs_rename_success_envelope(tools):
    tools.fs_create("test-rename-src.md", _page("test-rename-src"))
    src_stat = tools.fs_stat("test-rename-src.md")
    original_id = src_stat["resource_id"]
    result = tools.fs_rename("test-rename-src.md", "test-rename-dst.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-rename-dst.md"
    assert result["resource_id"] == original_id
    assert result["git"]["committed"] is True
    assert tools.fs_stat("test-rename-src.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_rename_preserves_id(tools):
    tools.fs_create("test-rename-id.md", _page("test-rename-id"))
    src_stat = tools.fs_stat("test-rename-id.md")
    original_id = src_stat["resource_id"]
    result = tools.fs_rename("test-rename-id.md", "test-rename-id-dst.md")
    assert result["resource_id"] == original_id
    dst_content = tools.fs_read("test-rename-id-dst.md")["content"]
    assert f"id: {original_id}" in dst_content


def test_fs_rename_source_not_found(tools):
    result = tools.fs_rename("no-src.md", "dst.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_rename_dest_exists(tools):
    tools.fs_create("test-rename-src2.md", _page("test-rename-src2"))
    tools.fs_create("test-rename-dst2.md", _page("test-rename-dst2"))
    result = tools.fs_rename("test-rename-src2.md", "test-rename-dst2.md")
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_rename_with_resource_id_match(tools):
    tools.fs_create("test-rename-rid.md", _page("test-rename-rid"))
    stat = tools.fs_stat("test-rename-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_rename("test-rename-rid.md", "test-rename-rid-dst.md",
                             resource_id=rid)
    assert result["node_type"] == "file"
    assert result["resource_id"] == rid


def test_fs_rename_with_resource_id_mismatch(tools):
    tools.fs_create("test-rename-rid-mm.md", _page("test-rename-rid-mm"))
    result = tools.fs_rename("test-rename-rid-mm.md", "test-rename-rid-mm-dst.md",
                             resource_id="w-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_rename_with_resource_id_tombstoned(tools):
    tools.fs_create("test-rename-rid-tomb.md", _page("test-rename-rid-tomb"))
    stat = tools.fs_stat("test-rename-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-rename-rid-tomb.md")
    result = tools.fs_rename("test-rename-rid-tomb.md", "test-rename-rid-tomb-dst.md",
                             resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_rename_path_traversal_source_rejected(tools):
    result = tools.fs_rename("../etc/passwd", "dst.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_rename_path_traversal_dest_rejected(tools):
    tools.fs_create("src.md", _page("src"))
    result = tools.fs_rename("src.md", "../etc/passwd")
    assert result["code"] == "INVALID_PATH"


# ── fs_delete (tombstone) ────────────────────────────────────────────────────

def test_fs_delete_success_envelope(tools):
    tools.fs_create("test-delete.md", _page("test-delete"))
    stat = tools.fs_stat("test-delete.md")
    rid = stat["resource_id"]
    result = tools.fs_delete("test-delete.md")
    assert result["node_type"] == "file"
    assert result["resource_id"] == rid
    assert result["git"]["committed"] is True
    assert tools.fs_stat("test-delete.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_delete_tombstone_id_not_reused(tools):
    tools.fs_create("test-delete-tomb.md", _page("test-delete-tomb"))
    stat = tools.fs_stat("test-delete-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-delete-tomb.md")
    assert tools._binding.ledger.is_tombstoned(rid)
    new = tools.fs_create("test-delete-new.md", _page("test-delete-new"))
    new_rid = new["resource_id"]
    assert new_rid != rid


def test_fs_delete_not_found(tools):
    result = tools.fs_delete("no-such.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_delete_via_mcp(srv):
    mcp, repo, tools = srv
    create_result = _call(mcp, "fs_create", {
        "path": "test-mcp-delete.md",
        "content": _page("test-mcp-delete"),
    })
    rid = create_result["resource_id"]
    del_result = _call(mcp, "fs_delete", {"path": "test-mcp-delete.md"})
    assert del_result["resource_id"] == rid
    assert del_result["git"]["committed"] is True


def test_fs_delete_with_resource_id_match(tools):
    tools.fs_create("test-delete-rid.md", _page("test-delete-rid"))
    stat = tools.fs_stat("test-delete-rid.md")
    rid = stat["resource_id"]
    result = tools.fs_delete("test-delete-rid.md", resource_id=rid)
    assert result["resource_id"] == rid


def test_fs_delete_with_resource_id_mismatch(tools):
    tools.fs_create("test-delete-rid-mm.md", _page("test-delete-rid-mm"))
    result = tools.fs_delete("test-delete-rid-mm.md", resource_id="w-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_delete_with_resource_id_tombstoned(tools):
    tools.fs_create("test-delete-rid-tomb.md", _page("test-delete-rid-tomb"))
    stat = tools.fs_stat("test-delete-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-delete-rid-tomb.md")
    result = tools.fs_delete("test-delete-rid-tomb.md", resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_delete_path_traversal_rejected(tools):
    result = tools.fs_delete("../etc/passwd")
    assert result["code"] == "INVALID_PATH"


# ── fs_batch (all-or-nothing + expected_base_commit) ─────────────────────────

def test_fs_batch_success(tools):
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-1.md", "content": _page("batch-1")}},
        {"op": "fs_create", "args": {"path": "batch-2.md", "content": _page("batch-2")}},
    ])
    assert result["node_type"] == "batch"
    assert "batch_results" in result
    assert len(result["batch_results"]) == 2
    assert result["batch_results"][0]["op"] == "fs_create"
    assert result["batch_results"][1]["op"] == "fs_create"
    assert result["git"]["committed"] is True
    assert tools.fs_stat("batch-1.md")["node_type"] == "file"
    assert tools.fs_stat("batch-2.md")["node_type"] == "file"


def test_fs_batch_all_or_nothing(tools):
    tools.fs_create("batch-existing.md", _page("batch-existing"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-new.md", "content": _page("batch-new")}},
        {"op": "fs_create", "args": {"path": "batch-existing.md", "content": _page("batch-existing")}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"
    assert tools.fs_stat("batch-new.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_expected_base_commit_cas(tools):
    tools.fs_create("batch-cas-1.md", _page("batch-cas-1"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("batch-cas-2.md", _page("batch-cas-2"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-cas-3.md", "content": _page("batch-cas-3")}},
    ], expected_base_commit=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


def test_fs_batch_expected_base_commit_success(tools):
    tools.fs_create("batch-commit-1.md", _page("batch-commit-1"))
    from katana_kernel import head_sha
    sha = head_sha(tools._repo_root)
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-commit-2.md", "content": _page("batch-commit-2")}},
    ], expected_base_commit=sha)
    assert result["node_type"] == "batch"


def test_fs_batch_broken_direct_returns_machine_envelope(tools, monkeypatch):
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["batch-broken.md"]},
    )

    def _raise_broken(*args, **kwargs):
        raise broken

    monkeypatch.setattr(tools._kernel, "mutate", _raise_broken)
    result = tools.fs_batch([
        {"op": "fs_create", "args": {
            "path": "batch-broken.md",
            "content": _page("batch-broken"),
        }},
    ])

    assert result["code"] == result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert result["manual_recovery_required"] is True
    assert "git" not in result


def test_fs_batch_broken_via_mcp_returns_machine_envelope(srv, monkeypatch):
    mcp, _, tools = srv
    broken = MutationBrokenError(
        "manual recovery required",
        {"state": "BROKEN", "paths": ["mcp-batch-broken.md"]},
    )

    def _raise_broken(*args, **kwargs):
        raise broken

    monkeypatch.setattr(tools._kernel, "mutate", _raise_broken)
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_create", "args": {
                "path": "mcp-batch-broken.md",
                "content": _page("mcp-batch-broken"),
            }},
        ],
    })

    assert result["code"] == result["state"] == "BROKEN"
    assert result["blocked"] is True
    assert result["manual_recovery_required"] is True
    assert "git" not in result


def test_fs_batch_edit(tools):
    tools.fs_create("batch-edit.md", _page("batch-edit"))
    result = tools.fs_batch([
        {"op": "fs_edit", "args": {
            "path": "batch-edit.md",
            "old_string": "正文内容",
            "new_string": "Batched edit content.",
        }},
    ])
    assert result["node_type"] == "batch"
    assert "Batched edit content." in tools.fs_read("batch-edit.md")["content"]


def test_fs_batch_copy(tools):
    tools.fs_create("batch-copy-src.md", _page("batch-copy-src"))
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {
            "source": "batch-copy-src.md",
            "dest": "batch-copy-dst.md",
        }},
    ])
    assert result["node_type"] == "batch"
    assert tools.fs_stat("batch-copy-dst.md")["node_type"] == "file"


def test_fs_batch_rename(tools):
    tools.fs_create("batch-rename-src.md", _page("batch-rename-src"))
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {
            "source": "batch-rename-src.md",
            "dest": "batch-rename-dst.md",
        }},
    ])
    assert result["node_type"] == "batch"
    assert tools.fs_stat("batch-rename-dst.md")["node_type"] == "file"
    assert tools.fs_stat("batch-rename-src.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_delete(tools):
    tools.fs_create("batch-del.md", _page("batch-del"))
    stat = tools.fs_stat("batch-del.md")
    rid = stat["resource_id"]
    result = tools.fs_batch([
        {"op": "fs_delete", "args": {"path": "batch-del.md"}},
    ])
    assert result["node_type"] == "batch"
    assert tools.fs_stat("batch-del.md")["code"] == "RESOURCE_NOT_FOUND"
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
    mcp, repo, tools = srv
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_create", "args": {"path": "mcp-batch-1.md", "content": _page("mcp-batch-1")}},
            {"op": "fs_create", "args": {"path": "mcp-batch-2.md", "content": _page("mcp-batch-2")}},
        ],
    })
    assert result["node_type"] == "batch"
    assert len(result["batch_results"]) == 2


def test_fs_batch_policy_enforced(tools):
    bad = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
---

无出链无来源无法验证。
"""
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-policy.md", "content": bad}},
    ])
    assert result["code"] == "INVALID_CONTENT"
    assert tools.fs_stat("batch-policy.md")["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_no_partial_mutation(tools):
    tools.fs_create("batch-partial.md", _page("batch-partial"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-partial-new.md", "content": _page("batch-partial-new")}},
        {"op": "fs_edit", "args": {
            "path": "batch-partial.md",
            "old_string": "nonexistent",
            "new_string": "replacement",
        }},
    ])
    assert result["code"] == "INVALID_CONTENT"
    assert tools.fs_stat("batch-partial-new.md")["code"] == "RESOURCE_NOT_FOUND"


# ── Batch error codes ────────────────────────────────────────────────────────

def test_fs_batch_create_path_traversal_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "../test.md", "content": _page("test")}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_write_path_traversal_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "../test.md", "content": _page("test")}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_edit_path_traversal_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_edit", "args": {"path": "../test.md", "old_string": "a", "new_string": "b"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_copy_source_path_traversal_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "../test.md", "dest": "dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_copy_dest_path_traversal_rejected(tools):
    tools.fs_create("batch-copy-src-ct.md", _page("batch-copy-src-ct"))
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "batch-copy-src-ct.md", "dest": "../dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_rename_source_path_traversal_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "../test.md", "dest": "dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_rename_dest_path_traversal_rejected(tools):
    tools.fs_create("batch-rename-src-ct.md", _page("batch-rename-src-ct"))
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "batch-rename-src-ct.md", "dest": "../dst.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_delete_path_traversal_rejected(tools):
    result = tools.fs_batch([
        {"op": "fs_delete", "args": {"path": "../test.md"}},
    ])
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_content_too_large(tools):
    big = "x" * 1_100_000
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-big.md", "content": big}},
    ])
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_fs_batch_write_content_too_large(tools):
    tools.fs_create("batch-write-big.md", _page("batch-write-big"))
    big = "x" * 1_100_000
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "batch-write-big.md", "content": big}},
    ])
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_fs_batch_resource_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "no-such-file.md", "content": _page("test")}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_edit_resource_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_edit", "args": {"path": "no-such-file.md", "old_string": "a", "new_string": "b"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_copy_source_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "no-src.md", "dest": "dst.md"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_rename_source_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "no-src.md", "dest": "dst.md"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_delete_not_found(tools):
    result = tools.fs_batch([
        {"op": "fs_delete", "args": {"path": "no-such-file.md"}},
    ])
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_batch_resource_exists(tools):
    tools.fs_create("batch-exists.md", _page("batch-exists"))
    result = tools.fs_batch([
        {"op": "fs_create", "args": {"path": "batch-exists.md", "content": _page("batch-exists")}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_batch_copy_dest_exists(tools):
    tools.fs_create("batch-copy-exists-src.md", _page("batch-copy-exists-src"))
    tools.fs_create("batch-copy-exists-dst.md", _page("batch-copy-exists-dst"))
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "batch-copy-exists-src.md", "dest": "batch-copy-exists-dst.md"}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_batch_rename_dest_exists(tools):
    tools.fs_create("batch-rename-exists-src.md", _page("batch-rename-exists-src"))
    tools.fs_create("batch-rename-exists-dst.md", _page("batch-rename-exists-dst"))
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "batch-rename-exists-src.md", "dest": "batch-rename-exists-dst.md"}},
    ])
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_batch_rename_rejects_no_resource_id(tools):
    content = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
source_type: human
credibility: high
sources:
  - 测试来源
摘要: 无ID页面
---

正文，包含 [[测试]]。

# References

- 测试
"""
    tools._vfs.write("batch-rename-noid-src.md", content, op="fs_create", args={})
    result = tools.fs_batch([
        {"op": "fs_rename", "args": {"source": "batch-rename-noid-src.md", "dest": "batch-rename-noid-dst.md"}},
    ])
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_copy_enforces_wiki_invariants(tools):
    bad = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
---

无出链无来源无法验证。
"""
    tools._vfs.write("batch-copy-bad-src.md", bad, op="fs_create", args={})
    result = tools.fs_batch([
        {"op": "fs_copy", "args": {"source": "batch-copy-bad-src.md", "dest": "batch-copy-bad-dst.md"}},
    ])
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_resource_id_mismatch(tools):
    tools.fs_create("batch-write-rid-mm.md", _page("batch-write-rid-mm"))
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "batch-write-rid-mm.md", "content": _page("batch-write-rid-mm"), "resource_id": "w-999999"}},
    ])
    assert result["code"] == "REF_MISMATCH"


def test_fs_batch_resource_id_tombstoned(tools):
    tools.fs_create("batch-write-rid-tomb.md", _page("batch-write-rid-tomb"))
    stat = tools.fs_stat("batch-write-rid-tomb.md")
    rid = stat["resource_id"]
    tools.fs_delete("batch-write-rid-tomb.md")
    result = tools.fs_batch([
        {"op": "fs_write", "args": {"path": "batch-write-rid-tomb.md", "content": _page("batch-write-rid-tomb"), "resource_id": rid}},
    ])
    assert result["code"] == "RESOURCE_REPLACED"


# ── CAS / idempotency conflicts ──────────────────────────────────────────────

def test_fs_create_stale_cas(tools):
    tools.fs_create("cas-1.md", _page("cas-1"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("cas-2.md", _page("cas-2"))
    result = tools.fs_create("cas-3.md", _page("cas-3"),
                             expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


def test_fs_write_stale_cas(tools):
    tools.fs_create("cas-write.md", _page("cas-write"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("cas-write-other.md", _page("cas-write-other"))
    result = tools.fs_write("cas-write.md", _page("cas-write"),
                            expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"


def test_fs_delete_stale_cas(tools):
    tools.fs_create("cas-del.md", _page("cas-del"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("cas-del-other.md", _page("cas-del-other"))
    result = tools.fs_delete("cas-del.md", expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"


def test_fs_create_idempotency_conflict(tools):
    tools.fs_create("idem-1.md", _page("idem-1"),
                    idempotency_key="key-001")
    result = tools.fs_create("idem-2.md", _page("idem-2"),
                             idempotency_key="key-001")
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


def test_fs_write_idempotency_conflict(tools):
    tools.fs_create("idem-write.md", _page("idem-write"),
                    idempotency_key="key-write")
    result = tools.fs_write("idem-write.md", _page("idem-write"),
                            idempotency_key="key-write")
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


# ── REF_MISMATCH / RESOURCE_REPLACED ─────────────────────────────────────────

def test_fs_write_id_mismatch(tools):
    tools.fs_create("write-mismatch.md", _page("write-mismatch"))
    modified = _page("write-mismatch").replace("摘要: write-mism", "id: w-999999\n摘要: write-mism")
    result = tools.fs_write("write-mismatch.md", modified)
    assert result["code"] == "REF_MISMATCH"


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
    tools.fs_create("conflict-1.md", _page("conflict-1"))
    from katana_kernel import head_sha
    sha1 = head_sha(tools._repo_root)
    tools.fs_create("conflict-2.md", _page("conflict-2"))
    result = tools.fs_create("conflict-3.md", _page("conflict-3"),
                             expected_base_sha=sha1)
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert "expected_revision" in result


# ── Wiki hard invariants on fs_* write paths ─────────────────────────────────

def test_fs_create_rejects_missing_frontmatter(tools):
    bad = "just text without frontmatter"
    result = tools.fs_create("no-fm.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_rejects_missing_outlinks(tools):
    bad = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
source_type: human
credibility: high
sources:
  - 测试来源
摘要: 无出链
---

正文没有 wikilink。

# References

- 测试
"""
    result = tools.fs_create("no-outlink.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_rejects_missing_sources(tools):
    bad = """---
创建日期: 2026-07-08
tags:
  - test
类型: 卡片
source_type: human
credibility: high
摘要: 无来源
---

正文包含 [[链接]] 但没有来源。
"""
    result = tools.fs_create("no-sources.md", bad)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_write_rejects_invalid_page(tools):
    tools.fs_create("write-invalid.md", _page("write-invalid"))
    bad = "not a valid page at all"
    result = tools.fs_write("write-invalid.md", bad)
    assert result["code"] == "INVALID_CONTENT"


# ── No partial mutation on error ─────────────────────────────────────────────

def test_fs_edit_no_partial_mutation(tools):
    tools.fs_create("no-partial.md", _page("no-partial"))
    original = tools.fs_read("no-partial.md")["content"]
    result = tools.fs_edit("no-partial.md", "nonexistent", "replacement")
    assert result["code"] == "INVALID_CONTENT"
    after = tools.fs_read("no-partial.md")["content"]
    assert after == original


def test_fs_write_no_partial_mutation(tools):
    tools.fs_create("no-partial-write.md", _page("no-partial-write"))
    original = tools.fs_stat("no-partial-write.md")["content"]
    bad = _page("no-partial-write").replace("摘要: no-partial", "id: w-999999\n摘要: no-partial")
    result = tools.fs_write("no-partial-write.md", bad)
    assert result["code"] == "REF_MISMATCH"
    after = tools.fs_stat("no-partial-write.md")["content"]
    assert "id: w-999999" not in after


# ── Wiki-specific validation: CODE_TYPES hard requirements ───────────────────

def test_fs_create_code_type_requires_source_type_credibility(tools):
    bad = """---
创建日期: 2026-07-08
tags:
  - code
类型: 源码分析
sources:
  - 代码来源
摘要: 代码分析
---

代码分析正文，包含 [[代码链接]]。

# References

- 代码参考
"""
    result = tools.fs_create("code-no-st.md", bad)
    assert result["code"] == "INVALID_CONTENT"


# ── Excluded directories ─────────────────────────────────────────────────────

def test_fs_create_rejected_in_excluded_dir(tools):
    os.makedirs(os.path.join(tools._repo_root, ".obsidian"), exist_ok=True)
    result = tools.fs_create(".obsidian/test.md", _page("test"))
    assert result["code"] == "INVALID_PATH"


def test_fs_stat_rejected_in_excluded_dir(tools):
    os.makedirs(os.path.join(tools._repo_root, ".git"), exist_ok=True)
    result = tools.fs_stat(".git")
    assert result["code"] == "INVALID_PATH"
