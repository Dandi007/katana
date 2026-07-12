"""fs_* Full VFS contract tests for Work Folder app.

Covers every operation's success/error envelopes, invariants,
edge cases, resource-id-primary semantics, non-brief files,
batch per-op error codes, MCP wrapper triggerability,
append-only governance for critical files, work-folder directory
validation, and resume/BROKEN invariant enforcement per spec.
"""

import asyncio
import os
import re
import subprocess

import pytest
from fastmcp import Client

from katana_work_folder_mcp import server as _server_mod
from katana_work_folder_mcp.fs_tools import FSTools, ID_RE
from katana_work_folder_mcp.store import _wf_policy


def _brief(title="Test Work Folder", goal="Test a work folder", rid="wf-abc123"):
    return f"""---
id: {rid}
title: {title}
status: active
created: "2026-07-08"
updated: "2026-07-08"
tags: []
kind: ""
links: []
---
\n**Goal:** {goal}

Summary text.
"""


def _brief_no_id(title="Test Work Folder", goal="Test a work folder"):
    return f"""---
title: {title}
status: active
created: "2026-07-08"
updated: "2026-07-08"
tags: []
kind: ""
links: []
---
\n**Goal:** {goal}

Summary text.
"""


def _progress_md():
    return """# Progress

**Goal:** Test work folder
**Status:** active
**Phase:** implementation
**Updated:** 2026-07-08

## Completed
- 

## Current
- 

## Blocked
- None

## Next
- 

## Changelog
| Time | Action | Detail |
|------|--------|--------|
| 10:00:00 | checkpoint | initial commit |
"""


def _context_md():
    return """# Context

**Updated:** 2026-07-08

## 工作上下文
- 

## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|

## 环境信息
- 
"""


def _claude_md():
    return """# Resume Guide

> 由 checkpoint 自动生成。上次更新：2026-07-08

## Goal
Test work folder

## Status
- **Phase:** implementation
- **Status:** active
- **Work folder:** /tmp/test

## Key Context
testing

## Key Decisions
暂无

## Known Issues
暂无

## Lessons
暂无

## Resume Steps
1. 阅读 progress.md 了解当前进度
2. 阅读 context.md 了解环境状态
3. 如有 spec.md / plan.md，阅读了解设计与计划
4. 继续 progress.md 中 Current/Next 列出的任务
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


def _mkdir(tools, dirname):
    os.makedirs(os.path.join(tools._repo_root, dirname), exist_ok=True)


def _create_file(tools, path, content):
    tools._vfs.write(path, content)
    subprocess.run(["git", "add", "."], cwd=tools._repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tools._repo_root, check=True, capture_output=True)


def _setup_work_folder(tools, dirname):
    """Create a valid work-folder directory with progress.md and _brief.md."""
    _mkdir(tools, dirname)
    _create_file(tools, f"{dirname}/progress.md", _progress_md())
    _create_file(tools, f"{dirname}/_brief.md", _brief_no_id(f"wf-{dirname}"))


def _setup_work_folder_with_context(tools, dirname):
    """Create a valid work-folder directory with progress.md, context.md, CLAUDE.md, and _brief.md."""
    _mkdir(tools, dirname)
    _create_file(tools, f"{dirname}/progress.md", _progress_md())
    _create_file(tools, f"{dirname}/context.md", _context_md())
    _create_file(tools, f"{dirname}/CLAUDE.md", _claude_md())
    _create_file(tools, f"{dirname}/_brief.md", _brief_no_id(f"wf-{dirname}"))


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
    ledger = ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json"), prefix="wf-")
    manifest = TransactionManifest(os.path.join(repo, ".katana", "manifests"))
    policy = _wf_policy()
    kernel.bind("work-folder", policy, vfs, ledger, manifest, repo)
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
    ledger = ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json"), prefix="wf-")
    manifest = TransactionManifest(os.path.join(repo, ".katana", "manifests"))
    policy = _wf_policy()
    kernel.bind("work-folder", policy, vfs, ledger, manifest, repo)
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


# ── fs_resolve ───────────────────────────────────────────────────────────────

def test_fs_resolve_by_path(tools):
    _setup_work_folder(tools, "test-resolve")
    result = tools.fs_resolve("test-resolve/_brief.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-resolve/_brief.md"
    assert result["resource_id"] is not None
    assert ID_RE.fullmatch(result["resource_id"])
    assert result["content_hash"] is not None
    assert result["content_hash"].startswith("sha256:")
    assert result["resource_revision"] is not None
    assert result["content_revision"] is not None
    assert result["commit"] is not None


def test_fs_resolve_by_id(tools):
    _setup_work_folder(tools, "test-resolve-id")
    stat = tools.fs_stat("test-resolve-id/_brief.md")
    rid = stat["resource_id"]
    result = tools.fs_resolve(rid)
    assert result["resource_id"] == rid
    assert result["virtual_path"] == "test-resolve-id/_brief.md"


def test_fs_resolve_not_found(tools):
    result = tools.fs_resolve("nonexistent.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_resolve_bad_id(tools):
    result = tools.fs_resolve("wf-ffffff")
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert result["resource_id"] == "wf-ffffff"


def test_fs_resolve_tombstoned_returns_replaced(tools):
    _setup_work_folder(tools, "tomb-resolve")
    stat = tools.fs_stat("tomb-resolve/_brief.md")
    rid = stat["resource_id"]
    tools.fs_delete("tomb-resolve/_brief.md")
    result = tools.fs_resolve(rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_resolve_path_traversal_rejected(tools):
    result = tools.fs_resolve("../etc/passwd")
    assert result["code"] == "INVALID_PATH"


def test_fs_resolve_root_level_brief_ignored(tools):
    _create_file(tools, "_brief.md", _brief_no_id("root-brief"))
    result = tools.fs_resolve("_brief.md")
    assert result["resource_id"] is None


def test_fs_resolve_non_work_folder_brief_ignored(tools):
    _mkdir(tools, "not-a-wf")
    _create_file(tools, "not-a-wf/_brief.md", _brief_no_id("not-a-wf"))
    result = tools.fs_resolve("not-a-wf/_brief.md")
    assert result["resource_id"] is None


# ── fs_stat ──────────────────────────────────────────────────────────────────

def test_fs_stat_file_success_envelope(tools):
    _setup_work_folder(tools, "test-stat")
    result = tools.fs_stat("test-stat/_brief.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-stat/_brief.md"
    assert result["resource_id"] is not None
    assert ID_RE.fullmatch(result["resource_id"])
    assert result["size"] > 0
    assert result["media_type"] == "text/markdown"
    assert result["content_hash"] is not None
    assert result["content_hash"].startswith("sha256:")
    assert result["resource_revision"] is not None
    assert result["content_revision"] is not None
    assert result["commit"] is not None


def test_fs_stat_non_brief_file(tools):
    _setup_work_folder(tools, "test-stat-nonbrief")
    tools.fs_create("notes.md", "# Notes\n\nChangelog entry.")
    result = tools.fs_stat("notes.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "notes.md"
    assert result["resource_id"] is None
    assert result["content_hash"] is not None
    assert result["content"].startswith("# Notes")


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
    _setup_work_folder(tools, "test-list-1")
    _setup_work_folder(tools, "test-list-2")
    result = tools.fs_list("")
    assert result["node_type"] == "directory"
    assert "entries" in result
    paths = [e["virtual_path"] for e in result["entries"]]
    assert "test-list-1/_brief.md" in paths
    assert "test-list-2/_brief.md" in paths
    for entry in result["entries"]:
        if entry["virtual_path"].endswith("_brief.md"):
            assert entry["resource_id"] is not None
        assert entry["node_type"] == "file"
        assert entry["size"] > 0
        assert entry["media_type"] == "text/markdown"
        assert entry["content_hash"] is not None


def test_fs_list_includes_non_brief_files(tools):
    _setup_work_folder(tools, "test-list-brief")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    result = tools.fs_list("")
    paths = [e["virtual_path"] for e in result["entries"]]
    assert "test-list-brief/_brief.md" in paths
    assert "notes.md" in paths
    for entry in result["entries"]:
        if entry["virtual_path"] == "notes.md":
            assert entry["resource_id"] is None


def test_fs_list_directory(tools):
    os.makedirs(os.path.join(tools._repo_root, "listdir"))
    _setup_work_folder(tools, "listdir/test-list-1")
    _setup_work_folder(tools, "listdir/test-list-2")
    result = tools.fs_list("")
    assert result["node_type"] == "directory"
    assert "entries" in result
    paths = [e["virtual_path"] for e in result["entries"]]
    assert "listdir/test-list-1/_brief.md" in paths
    assert "listdir/test-list-2/_brief.md" in paths


def test_fs_list_empty_root(tools):
    result = tools.fs_list("")
    assert result["node_type"] == "directory"
    assert "entries" in result


def test_fs_list_nonexistent_dir(tools):
    result = tools.fs_list("nonexistent")
    assert result["code"] in ("INVALID_PATH", "RESOURCE_NOT_FOUND")


# ── fs_glob ──────────────────────────────────────────────────────────────────

def test_fs_glob_match(tools):
    _setup_work_folder(tools, "test-glob-1")
    _setup_work_folder(tools, "test-glob-2")
    result = tools.fs_glob("test-glob-*/_brief.md")
    assert result["node_type"] == "glob"
    assert "hits" in result
    assert len(result["hits"]) == 2


def test_fs_glob_no_match(tools):
    result = tools.fs_glob("nonexistent-*.md")
    assert result["node_type"] == "glob"
    assert "hits" in result
    assert len(result["hits"]) == 0


def test_fs_glob_path_traversal_rejected(tools):
    result = tools.fs_glob("../*.md")
    assert result["code"] == "INVALID_PATH"


# ── fs_read ──────────────────────────────────────────────────────────────────

def test_fs_read_success_envelope(tools):
    _setup_work_folder(tools, "test-read")
    result = tools.fs_read("test-read/_brief.md")
    assert result["node_type"] == "file"
    assert result["resource_id"] is not None
    assert result["content"] is not None
    assert result["content_hash"] is not None
    assert result["content_hash"] == _hash(result["content"])
    assert "content_field" in result
    assert "total_lines" in result
    assert result["total_lines"] > 0


def _hash(s):
    import hashlib
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_fs_read_non_brief_file(tools):
    _setup_work_folder(tools, "test-read-nonbrief")
    tools.fs_create("notes.md", "# Notes\n\nLine 1\nLine 2")
    result = tools.fs_read("notes.md")
    assert result["node_type"] == "file"
    assert result["resource_id"] is None
    assert result["content_hash"] == _hash(result["content"])


def test_fs_read_offset_limit(tools):
    _setup_work_folder(tools, "test-read-offset")
    result = tools.fs_read("test-read-offset/_brief.md", offset=1, limit=1)
    assert result["offset"] == 1
    assert result["limit"] == 1


def test_fs_read_not_found(tools):
    result = tools.fs_read("nope.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_read_path_traversal_rejected(tools):
    result = tools.fs_read("../secret.txt")
    assert result["code"] == "INVALID_PATH"


# ── fs_create ────────────────────────────────────────────────────────────────

def test_fs_create_success_envelope(tools):
    _setup_work_folder(tools, "test-create")
    result = tools.fs_create("test-create/_brief.md", _brief_no_id("test-create"))
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-create/_brief.md"
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


def test_fs_create_non_brief_file(tools):
    _setup_work_folder(tools, "test-create-nonbrief")
    result = tools.fs_create("notes.md", "# Notes\n\ncontent")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "notes.md"
    assert result["resource_id"] is None
    assert result["content_hash"] is not None


def test_fs_create_auto_generates_id(tools):
    _setup_work_folder(tools, "test-auto-id")
    result = tools.fs_create("test-auto-id/_brief.md", _brief_no_id("test-auto-id"))
    rid = result["resource_id"]
    assert ID_RE.fullmatch(rid)
    assert rid.startswith("wf-")


def test_fs_create_resource_id_prefix(tools):
    _setup_work_folder(tools, "test-wf-prefix")
    result = tools.fs_create("test-wf-prefix/_brief.md", _brief_no_id("test-wf-prefix"))
    assert result["resource_id"].startswith("wf-")
    assert len(result["resource_id"]) == 9


def test_fs_create_duplicate_path_rejected(tools):
    _setup_work_folder(tools, "test-dup")
    result = tools.fs_create("test-dup/_brief.md", _brief_no_id("test-dup"))
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_create_invalid_content_rejected(tools):
    _setup_work_folder(tools, "test-invalid")
    result = tools.fs_create("test-invalid/_brief.md", "not valid brief content")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_create_content_too_large(tools):
    _setup_work_folder(tools, "test-big")
    big = "x" * 2_000_000
    result = tools.fs_create("test-big.md", big)
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_fs_create_path_traversal_rejected(tools):
    result = tools.fs_create("../escape/_brief.md", _brief_no_id("escape"))
    assert result["code"] == "INVALID_PATH"


def test_fs_create_cas_rejects_stale_sha(tools):
    _setup_work_folder(tools, "test-stale")
    result = tools.fs_create(
        "test-stale/_brief.md", _brief_no_id("test-stale"),
        expected_base_commit="a" * 40,
    )
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


def test_fs_create_critical_file_rejected(tools):
    _setup_work_folder(tools, "test-crit-create")
    result = tools.fs_create("progress.md", "## critical content")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_create_critical_golden_order_rejected(tools):
    _setup_work_folder(tools, "test-crit-go")
    result = tools.fs_create("golden-order.md", "## critical content")
    assert result["code"] == "POLICY_VIOLATION"


# ── Work-folder directory validation tests ───────────────────────────────────

def test_fs_create_brief_in_non_work_folder_rejected(tools):
    _mkdir(tools, "not-a-wf")
    result = tools.fs_create("not-a-wf/_brief.md", _brief_no_id("not-a-wf"))
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_create_brief_at_root_rejected(tools):
    result = tools.fs_create("_brief.md", _brief_no_id("root-brief"))
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_scan_ignores_root_level_brief(tools):
    _create_file(tools, "_brief.md", _brief_no_id("root-brief"))
    briefs = tools._scan_briefs()
    assert len(briefs) == 0


def test_fs_scan_ignores_non_work_folder_brief(tools):
    _mkdir(tools, "not-a-wf")
    _create_file(tools, "not-a-wf/_brief.md", _brief_no_id("not-a-wf"))
    briefs = tools._scan_briefs()
    assert len(briefs) == 0


def test_fs_scan_includes_work_folder_brief(tools):
    _setup_work_folder(tools, "valid-wf")
    briefs = tools._scan_briefs()
    assert len(briefs) == 1
    assert briefs[0]["path"] == "valid-wf/_brief.md"


# ── fs_write ─────────────────────────────────────────────────────────────────

def test_fs_write_success_envelope(tools):
    _setup_work_folder(tools, "test-write")
    result = tools.fs_create("test-write/_brief.md", _brief_no_id("test-write"))
    rid = result["resource_id"]
    content = _brief("test-write-updated", rid=rid)
    result = tools.fs_write("test-write/_brief.md", content)
    assert result["node_type"] == "file"
    assert result["resource_id"] == rid
    assert result["content_hash"] is not None
    assert "git" in result
    assert result["git"]["committed"] is True


def test_fs_write_does_not_implicitly_create(tools):
    result = tools.fs_write("nonexistent.md", _brief("nonexistent"))
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_write_id_immutable(tools):
    _setup_work_folder(tools, "test-immutable")
    result = tools.fs_create("test-immutable/_brief.md", _brief_no_id("test-immutable"))
    rid = result["resource_id"]
    content = _brief("test-immutable-changed", rid="wf-def456")
    result = tools.fs_write("test-immutable/_brief.md", content)
    assert result["code"] == "REF_MISMATCH"


def test_fs_write_ref_mismatch(tools):
    _setup_work_folder(tools, "test-ref-mismatch")
    result = tools.fs_create("test-ref-mismatch/_brief.md", _brief_no_id("test-ref-mismatch"))
    rid = result["resource_id"]
    content = _brief("test-ref-mismatch", rid=rid)
    result = tools.fs_write("test-ref-mismatch/_brief.md", content, resource_id="wf-999999")
    assert result["code"] in ("REF_MISMATCH", "RESOURCE_NOT_FOUND")


def test_fs_write_revision_conflict(tools):
    _setup_work_folder(tools, "test-rev-conflict")
    result = tools.fs_create("test-rev-conflict/_brief.md", _brief_no_id("test-rev-conflict"))
    rid = result["resource_id"]
    content = _brief("test-rev-conflict-updated", rid=rid)
    result = tools.fs_write(
        "test-rev-conflict/_brief.md", content,
        expected_resource_revision="sha256:deadbeef",
    )
    assert result["code"] == "REVISION_CONFLICT"
    assert result["retryable"] is True


def test_fs_write_not_found(tools):
    result = tools.fs_write("nope.md", _brief("nope"))
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_write_non_brief_file(tools):
    _setup_work_folder(tools, "test-write-nonbrief")
    tools.fs_create("notes.md", "# Notes\n\nOld content")
    result = tools.fs_write("notes.md", "# Notes\n\nNew content")
    assert result["node_type"] == "file"
    assert result["resource_id"] is None
    assert "New content" in result["content"]


def test_fs_write_resource_id_auto_resolved(tools):
    _setup_work_folder(tools, "test-auto-resolve")
    result = tools.fs_create("test-auto-resolve/_brief.md", _brief_no_id("test-auto-resolve"))
    rid = result["resource_id"]
    content = _brief("test-auto-resolve-updated", rid=rid)
    result = tools.fs_write("test-auto-resolve/_brief.md", content)
    assert result["resource_id"] == rid


def test_fs_write_resource_id_resolved_even_without_param(tools):
    _setup_work_folder(tools, "test-no-rid")
    result = tools.fs_create("test-no-rid/_brief.md", _brief_no_id("test-no-rid"))
    rid = result["resource_id"]
    content = _brief("test-no-rid-updated", rid=rid)
    result = tools.fs_write("test-no-rid/_brief.md", content)
    assert result["resource_id"] == rid


# ── Append-only tests for critical files ─────────────────────────────────────

def test_fs_write_progress_append_only_allowed(tools):
    _setup_work_folder(tools, "test-progress-append")
    old_content = tools.fs_read("test-progress-append/progress.md")["content"]
    new_content = old_content + "\n| 11:00:00 | test | appended entry |\n"
    result = tools.fs_write("test-progress-append/progress.md", new_content)
    assert result["node_type"] == "file"
    assert "appended entry" in result["content"]


def test_fs_write_progress_changelog_rewrite_rejected(tools):
    _setup_work_folder(tools, "test-progress-rewrite")
    old_content = tools.fs_read("test-progress-append/progress.md")["content"]
    new_content = old_content.replace("10:00:00", "99:99:99")
    result = tools.fs_write("test-progress-append/progress.md", new_content)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_write_progress_truncation_rejected(tools):
    _setup_work_folder(tools, "test-progress-trunc")
    old_content = tools.fs_read("test-progress-append/progress.md")["content"]
    truncated = old_content[:len(old_content) // 2]
    result = tools.fs_write("test-progress-append/progress.md", truncated)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_write_golden_order_append_only_allowed(tools):
    _setup_work_folder(tools, "test-go-append")
    _create_file(tools, "test-go-append/golden-order.md", "# Golden Order\n\nRule 1: First rule\n")
    result = tools.fs_write("test-go-append/golden-order.md",
        "# Golden Order\n\nRule 1: First rule\n\nRule 2: Second rule\n")
    assert result["node_type"] == "file"


def test_fs_write_golden_order_rewrite_rejected(tools):
    _setup_work_folder(tools, "test-go-rewrite")
    _create_file(tools, "test-go-rewrite/golden-order.md", "Rule 1: First rule\n")
    result = tools.fs_write("test-go-rewrite/golden-order.md", "Rule 2: Something else\n")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_delete_progress_rejected(tools):
    _setup_work_folder(tools, "test-del-progress")
    result = tools.fs_delete("test-del-progress/progress.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_delete_golden_order_rejected(tools):
    _setup_work_folder(tools, "test-del-go")
    _create_file(tools, "test-del-go/golden-order.md", "## golden order")
    result = tools.fs_delete("test-del-go/golden-order.md")
    assert result["code"] == "POLICY_VIOLATION"


# ── Resume/BROKEN invariant tests ────────────────────────────────────────────

def test_fs_write_claude_resume_guide_conserved(tools):
    _setup_work_folder_with_context(tools, "test-claude")
    old_content = tools.fs_read("test-claude/CLAUDE.md")["content"]
    new_content = old_content + "\n\nAdditional note at the end.\n"
    result = tools.fs_write("test-claude/CLAUDE.md", new_content)
    assert result["node_type"] == "file"


def test_fs_write_claude_resume_guide_removed_rejected(tools):
    _setup_work_folder_with_context(tools, "test-claude-rm")
    result = tools.fs_write("test-claude-rm/CLAUDE.md",
        "# Totally different content\n\nNo resume guide here.\n")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_write_agents_resume_guide_removed_rejected(tools):
    _setup_work_folder_with_context(tools, "test-agents-rm")
    result = tools.fs_write("test-agents-rm/AGENTS.md",
        "# Different content\n\nNo resume guide.\n")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_write_context_structure_conserved(tools):
    _setup_work_folder_with_context(tools, "test-ctx")
    old_content = tools.fs_read("test-ctx/context.md")["content"]
    new_content = old_content + "\n\nMore context details.\n"
    result = tools.fs_write("test-ctx/context.md", new_content)
    assert result["node_type"] == "file"


def test_fs_write_context_removed_rejected(tools):
    _setup_work_folder_with_context(tools, "test-ctx-rm")
    result = tools.fs_write("test-ctx-rm/context.md",
        "# Just a header\n\nNo context structure.\n")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_edit_claude_preserves_resume_guide(tools):
    _setup_work_folder_with_context(tools, "test-edit-claude")
    result = tools.fs_edit("test-edit-claude/CLAUDE.md",
        "暂无", "Some decisions were made")
    assert result["node_type"] == "file"


def test_fs_edit_claude_removes_section_rejected(tools):
    _setup_work_folder_with_context(tools, "test-edit-claude-rm")
    old_content = tools.fs_read("test-edit-claude-rm/CLAUDE.md")["content"]
    result = tools.fs_edit("test-edit-claude-rm/CLAUDE.md",
        "## Goal", "## No Goal")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_write_progress_broken_blocks_conserved(tools):
    _setup_work_folder(tools, "test-broken")
    old_content = tools.fs_read("test-broken/progress.md")["content"]
    new_content = old_content.replace("## Blocked\n- None", "## Blocked\n- BROKEN: resource unavailable")
    new_content += "\n| 11:00:00 | test | appended |\n"
    result = tools.fs_write("test-broken/progress.md", new_content)
    assert result["node_type"] == "file"


def test_fs_write_progress_broken_block_removed_rejected(tools):
    _setup_work_folder(tools, "test-broken-rm")
    old_content = tools.fs_read("test-broken-rm/progress.md")["content"]
    new_content = old_content.replace("## Blocked\n- None", "## Removed\n- None")
    new_content += "\n| 11:00:00 | test | appended |\n"
    result = tools.fs_write("test-broken-rm/progress.md", new_content)
    assert result["code"] == "POLICY_VIOLATION"


# ── fs_edit ──────────────────────────────────────────────────────────────────

def test_fs_edit_success(tools):
    _setup_work_folder(tools, "test-edit")
    result = tools.fs_create("test-edit/_brief.md", _brief_no_id("test-edit"))
    rid = result["resource_id"]
    result = tools.fs_edit("test-edit/_brief.md", "Test a work folder", "Updated work goal")
    assert result["node_type"] == "file"
    assert result["resource_id"] == rid
    assert "Updated work goal" in result["content"]


def test_fs_edit_exact_match(tools):
    _setup_work_folder(tools, "test-edit-exact")
    result = tools.fs_edit("test-edit-exact/_brief.md", "nonexistent string", "replacement")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_multiple_matches_no_replace_all(tools):
    _setup_work_folder(tools, "test-edit-multi")
    old = tools.fs_read("test-edit-multi/_brief.md")["content"]
    new = old.replace("Test a work folder", "duplicate duplicate goal")
    tools.fs_write("test-edit-multi/_brief.md", new)
    result = tools.fs_edit("test-edit-multi/_brief.md", "duplicate", "changed")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_replace_all(tools):
    _setup_work_folder(tools, "test-edit-all")
    result = tools.fs_edit("test-edit-all/_brief.md", "test", "CHANGED", replace_all=True)
    assert result["node_type"] == "file"


def test_fs_edit_id_immutable(tools):
    _setup_work_folder(tools, "test-edit-id")
    result = tools.fs_create("test-edit-id/_brief.md", _brief_no_id("test-edit-id"))
    rid = result["resource_id"]
    result = tools.fs_edit("test-edit-id/_brief.md", rid, "wf-999999")
    assert result["code"] == "REF_MISMATCH"


def test_fs_edit_not_found(tools):
    result = tools.fs_edit("nope.md", "old", "new")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_edit_empty_old_string(tools):
    _setup_work_folder(tools, "test-edit-empty")
    result = tools.fs_edit("test-edit-empty/_brief.md", "", "new")
    assert result["code"] == "INVALID_CONTENT"


def test_fs_edit_non_brief_file(tools):
    _setup_work_folder(tools, "test-edit-nonbrief")
    tools.fs_create("notes.md", "# Notes\n\nhello world")
    result = tools.fs_edit("notes.md", "hello", "goodbye")
    assert result["node_type"] == "file"
    assert "goodbye" in result["content"]


def test_fs_edit_progress_append_only_allowed(tools):
    _setup_work_folder(tools, "test-edit-progress")
    old_content = tools.fs_read("test-edit-progress/progress.md")["content"]
    result = tools.fs_edit("test-edit-progress/progress.md",
        "## Next\n- ", "## Next\n- New task\n")
    assert result["node_type"] == "file"
    assert "New task" in result["content"]


def test_fs_edit_progress_changelog_rewrite_rejected(tools):
    _setup_work_folder(tools, "test-edit-progress-rewrite")
    result = tools.fs_edit("test-edit-progress-rewrite/progress.md",
        "10:00:00", "99:99:99")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_edit_golden_order_append_only_allowed(tools):
    _setup_work_folder(tools, "test-edit-go")
    _create_file(tools, "test-edit-go/golden-order.md", "Rule 1: First rule\n")
    result = tools.fs_edit("test-edit-go/golden-order.md",
        "Rule 1: First rule", "Rule 1: First rule\n\nRule 2: Second rule")
    assert result["node_type"] == "file"


def test_fs_edit_golden_order_rewrite_rejected(tools):
    _setup_work_folder(tools, "test-edit-go-rewrite")
    _create_file(tools, "test-edit-go-rewrite/golden-order.md", "Rule 1: First rule\n")
    result = tools.fs_edit("test-edit-go-rewrite/golden-order.md",
        "Rule 1: First rule", "Rule 2: Different rule")
    assert result["code"] == "POLICY_VIOLATION"


# ── fs_copy ──────────────────────────────────────────────────────────────────

def test_fs_copy_success(tools):
    _setup_work_folder(tools, "test-copy-src")
    _setup_work_folder(tools, "test-copy-dst")
    result = tools.fs_copy("test-copy-src/_brief.md", "test-copy-dst/_brief.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-copy-dst/_brief.md"
    assert result["resource_id"] is not None
    assert result["resource_id"].startswith("wf-")


def test_fs_copy_new_id(tools):
    _setup_work_folder(tools, "test-copy-new-id")
    src_stat = tools.fs_stat("test-copy-new-id/_brief.md")
    src_rid = src_stat["resource_id"]
    _setup_work_folder(tools, "test-copy-new-dst")
    result = tools.fs_copy("test-copy-new-id/_brief.md", "test-copy-new-dst/_brief.md")
    assert result["resource_id"] != src_rid


def test_fs_copy_source_not_found(tools):
    result = tools.fs_copy("nonexistent.md", "dest.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_copy_dest_exists(tools):
    _setup_work_folder(tools, "test-copy-exists-src")
    _setup_work_folder(tools, "test-copy-exists-dst")
    result = tools.fs_copy("test-copy-exists-src/_brief.md", "test-copy-exists-dst/_brief.md")
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_copy_source_traversal_rejected(tools):
    result = tools.fs_copy("../escape/_brief.md", "dest.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_dest_traversal_rejected(tools):
    _setup_work_folder(tools, "test-copy-dest-trav")
    result = tools.fs_copy("test-copy-dest-trav/_brief.md", "../escape/_brief.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_copy_non_brief_file(tools):
    _setup_work_folder(tools, "test-copy-nonbrief")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    result = tools.fs_copy("notes.md", "notes-copy.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "notes-copy.md"
    assert result["resource_id"] is None


def test_fs_copy_critical_source_rejected(tools):
    _setup_work_folder(tools, "test-copy-crit-src")
    result = tools.fs_copy("test-copy-crit-src/progress.md", "backup.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_copy_critical_dest_rejected(tools):
    _setup_work_folder(tools, "test-copy-crit-dst")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    result = tools.fs_copy("notes.md", "progress.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_copy_non_brief_to_brief_rejected(tools):
    _setup_work_folder(tools, "test-copy-nb2b")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    _setup_work_folder(tools, "folder")
    result = tools.fs_copy("notes.md", "folder/_brief.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_copy_brief_to_non_brief_rejected(tools):
    _setup_work_folder(tools, "test-copy-b2nb")
    result = tools.fs_copy("test-copy-b2nb/_brief.md", "copy.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_copy_brief_to_non_work_folder_rejected(tools):
    _setup_work_folder(tools, "test-copy-b2nowf")
    _mkdir(tools, "not-a-wf")
    result = tools.fs_copy("test-copy-b2nowf/_brief.md", "not-a-wf/_brief.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_single_cas_expected_base_commit(tools):
    _setup_work_folder(tools, "test-cas-param")
    content = _brief("test-cas-param-updated", rid="wf-abc123")
    result = tools.fs_write(
        "test-cas-param/_brief.md", content,
        expected_base_commit="a" * 40,
    )
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


# ── fs_rename ────────────────────────────────────────────────────────────────

def test_fs_rename_success(tools):
    _setup_work_folder(tools, "test-rename-src")
    _setup_work_folder(tools, "test-rename-dst")
    result = tools.fs_rename("test-rename-src/_brief.md", "test-rename-dst/_brief.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "test-rename-dst/_brief.md"


def test_fs_rename_preserves_id(tools):
    _setup_work_folder(tools, "test-rename-id")
    stat = tools.fs_stat("test-rename-id/_brief.md")
    rid = stat["resource_id"]
    _setup_work_folder(tools, "test-rename-id-dst")
    result = tools.fs_rename("test-rename-id/_brief.md", "test-rename-id-dst/_brief.md")
    assert result["resource_id"] == rid


def test_fs_rename_source_not_found(tools):
    result = tools.fs_rename("nonexistent.md", "dest.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_rename_dest_exists(tools):
    _setup_work_folder(tools, "test-rename-exists-src")
    _setup_work_folder(tools, "test-rename-exists-dst")
    result = tools.fs_rename("test-rename-exists-src/_brief.md", "test-rename-exists-dst/_brief.md")
    assert result["code"] == "RESOURCE_EXISTS"


def test_fs_rename_source_traversal_rejected(tools):
    result = tools.fs_rename("../escape/_brief.md", "dest.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_rename_dest_traversal_rejected(tools):
    _setup_work_folder(tools, "test-rename-dest-trav")
    result = tools.fs_rename("test-rename-dest-trav/_brief.md", "../escape/_brief.md")
    assert result["code"] == "INVALID_PATH"


def test_fs_rename_non_brief_file(tools):
    _setup_work_folder(tools, "test-rename-nonbrief")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    result = tools.fs_rename("notes.md", "notes-renamed.md")
    assert result["node_type"] == "file"
    assert result["virtual_path"] == "notes-renamed.md"
    assert result["resource_id"] is None


def test_fs_rename_critical_source_rejected(tools):
    _setup_work_folder(tools, "test-rename-crit-src")
    result = tools.fs_rename("test-rename-crit-src/progress.md", "old.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_rename_critical_dest_rejected(tools):
    _setup_work_folder(tools, "test-rename-crit-dst")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    result = tools.fs_rename("notes.md", "progress.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_rename_non_brief_to_brief_rejected(tools):
    _setup_work_folder(tools, "test-rename-nb2b")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    _setup_work_folder(tools, "folder")
    result = tools.fs_rename("notes.md", "folder/_brief.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_rename_brief_to_non_brief_rejected(tools):
    _setup_work_folder(tools, "test-rename-b2nb")
    result = tools.fs_rename("test-rename-b2nb/_brief.md", "renamed.md")
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_rename_brief_to_non_work_folder_rejected(tools):
    _setup_work_folder(tools, "test-rename-b2nowf")
    _mkdir(tools, "not-a-wf")
    result = tools.fs_rename("test-rename-b2nowf/_brief.md", "not-a-wf/_brief.md")
    assert result["code"] == "POLICY_VIOLATION"


# ── fs_delete ────────────────────────────────────────────────────────────────

def test_fs_delete_success(tools):
    _setup_work_folder(tools, "test-delete")
    stat = tools.fs_stat("test-delete/_brief.md")
    rid = stat["resource_id"]
    result = tools.fs_delete("test-delete/_brief.md")
    assert result["node_type"] == "file"
    assert result["resource_id"] == rid
    assert result["size"] == 0
    assert result.get("content") is None


def test_fs_delete_leaves_tombstone(tools):
    _setup_work_folder(tools, "test-tombstone")
    result = tools.fs_delete("test-tombstone/_brief.md")
    rid = result["resource_id"]
    resolve_result = tools.fs_resolve(rid)
    assert resolve_result["code"] == "RESOURCE_REPLACED"


def test_fs_delete_id_not_reused(tools):
    _setup_work_folder(tools, "test-no-reuse")
    stat = tools.fs_stat("test-no-reuse/_brief.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-no-reuse/_brief.md")
    _setup_work_folder(tools, "test-no-reuse-2")
    result = tools.fs_create("test-no-reuse-2/_brief.md", _brief_no_id("test-no-reuse-2"), resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_fs_delete_not_found(tools):
    result = tools.fs_delete("nope.md")
    assert result["code"] == "RESOURCE_NOT_FOUND"


def test_fs_delete_ref_mismatch(tools):
    _setup_work_folder(tools, "test-del-ref")
    result = tools.fs_delete("test-del-ref/_brief.md", resource_id="wf-999999")
    assert result["code"] in ("REF_MISMATCH", "RESOURCE_NOT_FOUND")


def test_fs_delete_non_brief_file(tools):
    _setup_work_folder(tools, "test-del-nonbrief")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    result = tools.fs_delete("notes.md")
    assert result["node_type"] == "file"
    assert result["resource_id"] is None


# ── fs_batch ─────────────────────────────────────────────────────────────────

def test_fs_batch_success(tools):
    _setup_work_folder(tools, "batch-1")
    _setup_work_folder(tools, "batch-2")
    ops = [
        {"op": "fs_create", "args": {"path": "batch-1/_brief.md", "content": _brief_no_id("batch-1")}},
        {"op": "fs_create", "args": {"path": "batch-2/_brief.md", "content": _brief_no_id("batch-2")}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"
    assert "batch_results" in result
    assert len(result["batch_results"]) == 2
    assert result["batch_results"][0]["op"] == "fs_create"
    assert result["batch_results"][1]["op"] == "fs_create"


def test_fs_batch_write(tools):
    _setup_work_folder(tools, "batch-write")
    result = tools.fs_create("batch-write/_brief.md", _brief_no_id("batch-write"))
    rid = result["resource_id"]
    content = _brief("batch-write-updated", rid=rid)
    ops = [
        {"op": "fs_write", "args": {"path": "batch-write/_brief.md", "content": content}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"
    assert len(result["batch_results"]) == 1
    assert result["batch_results"][0]["op"] == "fs_write"


def test_fs_batch_edit(tools):
    _setup_work_folder(tools, "batch-edit")
    ops = [
        {"op": "fs_edit", "args": {"path": "batch-edit/_brief.md", "old_string": "Test a work folder", "new_string": "Changed"}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"
    assert "Changed" in tools.fs_read("batch-edit/_brief.md")["content"]


def test_fs_batch_copy(tools):
    _setup_work_folder(tools, "batch-copy-src")
    _setup_work_folder(tools, "batch-copy-dst")
    ops = [
        {"op": "fs_copy", "args": {"source": "batch-copy-src/_brief.md", "dest": "batch-copy-dst/_brief.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"
    assert result["batch_results"][0]["resource_id"].startswith("wf-")


def test_fs_batch_rename(tools):
    _setup_work_folder(tools, "batch-rename-src")
    _setup_work_folder(tools, "batch-rename-dst")
    stat = tools.fs_stat("batch-rename-src/_brief.md")
    rid = stat["resource_id"]
    ops = [
        {"op": "fs_rename", "args": {"source": "batch-rename-src/_brief.md", "dest": "batch-rename-dst/_brief.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"
    assert result["batch_results"][0]["resource_id"] == rid


def test_fs_batch_delete(tools):
    _setup_work_folder(tools, "batch-delete")
    stat = tools.fs_stat("batch-delete/_brief.md")
    rid = stat["resource_id"]
    ops = [
        {"op": "fs_delete", "args": {"path": "batch-delete/_brief.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"
    assert result["batch_results"][0]["resource_id"] == rid


def test_fs_batch_all_or_nothing(tools):
    _setup_work_folder(tools, "batch-aon")
    ops = [
        {"op": "fs_edit", "args": {"path": "batch-aon/_brief.md", "old_string": "Test a work folder", "new_string": "Modified goal"}},
        {"op": "fs_edit", "args": {"path": "batch-aon/_brief.md", "old_string": "nonexistent string", "new_string": "replacement"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "INVALID_CONTENT"
    assert "Modified goal" not in tools.fs_read("batch-aon/_brief.md").get("content", "")


def test_fs_batch_cas(tools):
    _setup_work_folder(tools, "batch-cas")
    stat = tools.fs_stat("batch-cas/_brief.md")
    rid = stat["resource_id"]
    ops = [
        {"op": "fs_write", "args": {"path": "batch-cas/_brief.md", "content": _brief("batch-cas-u", rid=rid)}},
    ]
    result = tools.fs_batch(ops, expected_base_commit="a" * 40)
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


def test_fs_batch_empty_operations(tools):
    result = tools.fs_batch([])
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_unknown_operation(tools):
    ops = [
        {"op": "fs_unknown", "args": {"path": "test.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "INVALID_CONTENT"


def test_fs_batch_idempotency(tools):
    _setup_work_folder(tools, "batch-idem")
    stat = tools.fs_stat("batch-idem/_brief.md")
    rid = stat["resource_id"]
    ops = [
        {"op": "fs_write", "args": {"path": "batch-idem/_brief.md", "content": _brief("batch-idem-u", rid=rid)}},
    ]
    result = tools.fs_batch(ops, idempotency_key="key-batch-1")
    assert result["node_type"] == "batch"
    result2 = tools.fs_batch(ops, idempotency_key="key-batch-1")
    assert result2["code"] == "IDEMPOTENCY_CONFLICT"


def test_fs_batch_path_traversal_rejected(tools):
    ops = [
        {"op": "fs_create", "args": {"path": "../escape/_brief.md", "content": _brief_no_id("escape")}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "INVALID_PATH"


def test_fs_batch_per_op_revision_conflict(tools):
    _setup_work_folder(tools, "batch-rev")
    ops = [
        {"op": "fs_write", "args": {
            "path": "batch-rev/_brief.md",
            "content": _brief("batch-rev-u", rid="wf-000000"),
            "expected_resource_revision": "sha256:deadbeef",
        }},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "REVISION_CONFLICT"


def test_fs_batch_critical_delete_rejected(tools):
    _setup_work_folder(tools, "batch-crit-del")
    ops = [
        {"op": "fs_delete", "args": {"path": "batch-crit-del/progress.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_critical_copy_rejected(tools):
    _setup_work_folder(tools, "batch-crit-copy")
    ops = [
        {"op": "fs_copy", "args": {"source": "batch-crit-copy/progress.md", "dest": "backup.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_critical_rename_rejected(tools):
    _setup_work_folder(tools, "batch-crit-rename")
    ops = [
        {"op": "fs_rename", "args": {"source": "batch-crit-rename/progress.md", "dest": "old.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_non_brief_files(tools):
    _setup_work_folder(tools, "batch-nonbrief")
    ops = [
        {"op": "fs_create", "args": {"path": "notes.md", "content": "# Notes\n\ncontent"}},
        {"op": "fs_create", "args": {"path": "batch-nonbrief/context.md", "content": _context_md()}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"
    assert len(result["batch_results"]) == 2


def test_fs_batch_non_brief_copy(tools):
    _setup_work_folder(tools, "batch-nonbrief-copy")
    tools.fs_create("batch-nonbrief-copy/context.md", _context_md())
    ops = [
        {"op": "fs_copy", "args": {"source": "batch-nonbrief-copy/context.md", "dest": "batch-nonbrief-copy/context2.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"


def test_fs_batch_copy_non_brief_to_brief_rejected(tools):
    _setup_work_folder(tools, "batch-nb2b")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    _setup_work_folder(tools, "folder")
    ops = [
        {"op": "fs_copy", "args": {"source": "notes.md", "dest": "folder/_brief.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_copy_brief_to_non_brief_rejected(tools):
    _setup_work_folder(tools, "batch-b2nb")
    ops = [
        {"op": "fs_copy", "args": {"source": "batch-b2nb/_brief.md", "dest": "copy.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_rename_non_brief_to_brief_rejected(tools):
    _setup_work_folder(tools, "batch-rnb2b")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    _setup_work_folder(tools, "folder")
    ops = [
        {"op": "fs_rename", "args": {"source": "notes.md", "dest": "folder/_brief.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_rename_brief_to_non_brief_rejected(tools):
    _setup_work_folder(tools, "batch-r2nb")
    ops = [
        {"op": "fs_rename", "args": {"source": "batch-r2nb/_brief.md", "dest": "renamed.md"}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


# ── Batch append-only and governed tests ─────────────────────────────────────

def test_fs_batch_progress_append_only_allowed(tools):
    _setup_work_folder(tools, "batch-progress-ok")
    old_content = tools.fs_read("batch-progress-ok/progress.md")["content"]
    new_content = old_content + "\n| 11:00:00 | test | batch append |\n"
    ops = [
        {"op": "fs_write", "args": {"path": "batch-progress-ok/progress.md", "content": new_content}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"


def test_fs_batch_progress_rewrite_rejected(tools):
    _setup_work_folder(tools, "batch-progress-rewrite")
    old_content = tools.fs_read("batch-progress-rewrite/progress.md")["content"]
    new_content = old_content.replace("10:00:00", "99:99:99")
    ops = [
        {"op": "fs_write", "args": {"path": "batch-progress-rewrite/progress.md", "content": new_content}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_golden_order_append_only_allowed(tools):
    _setup_work_folder(tools, "batch-go")
    _create_file(tools, "batch-go/golden-order.md", "Rule 1: First rule\n")
    ops = [
        {"op": "fs_write", "args": {
            "path": "batch-go/golden-order.md",
            "content": "Rule 1: First rule\n\nRule 2: Second rule\n",
        }},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"


def test_fs_batch_claude_resume_guide_conserved(tools):
    _setup_work_folder_with_context(tools, "batch-claude")
    old_content = tools.fs_read("batch-claude/CLAUDE.md")["content"]
    new_content = old_content + "\n\nAppended note.\n"
    ops = [
        {"op": "fs_write", "args": {"path": "batch-claude/CLAUDE.md", "content": new_content}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"


def test_fs_batch_claude_resume_guide_removed_rejected(tools):
    _setup_work_folder_with_context(tools, "batch-claude-rm")
    ops = [
        {"op": "fs_write", "args": {
            "path": "batch-claude-rm/CLAUDE.md",
            "content": "# Different\n\nNo resume guide.\n",
        }},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_context_conserved(tools):
    _setup_work_folder_with_context(tools, "batch-ctx")
    old_content = tools.fs_read("batch-ctx/context.md")["content"]
    new_content = old_content + "\n\nAppended context.\n"
    ops = [
        {"op": "fs_write", "args": {"path": "batch-ctx/context.md", "content": new_content}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"


def test_fs_batch_context_removed_rejected(tools):
    _setup_work_folder_with_context(tools, "batch-ctx-rm")
    ops = [
        {"op": "fs_write", "args": {
            "path": "batch-ctx-rm/context.md",
            "content": "# Just a header\n\nNo context.\n",
        }},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


def test_fs_batch_broken_blocks_conserved(tools):
    _setup_work_folder(tools, "batch-broken")
    old_content = tools.fs_read("batch-broken/progress.md")["content"]
    new_content = old_content.replace("## Blocked\n- None", "## Blocked\n- BROKEN: test")
    new_content += "\n| 11:00:00 | test | appended |\n"
    ops = [
        {"op": "fs_write", "args": {"path": "batch-broken/progress.md", "content": new_content}},
    ]
    result = tools.fs_batch(ops)
    assert result["node_type"] == "batch"


def test_fs_batch_non_work_folder_brief_rejected(tools):
    _mkdir(tools, "not-a-wf")
    ops = [
        {"op": "fs_create", "args": {"path": "not-a-wf/_brief.md", "content": _brief_no_id("not-a-wf")}},
    ]
    result = tools.fs_batch(ops)
    assert result["code"] == "POLICY_VIOLATION"


# ── error envelope tests ─────────────────────────────────────────────────────

def test_error_envelope_fields(tools):
    _setup_work_folder(tools, "test-error-env")
    result = tools.fs_resolve("nonexistent.md")
    assert "code" in result
    assert "message" in result
    assert "retryable" in result
    assert result["code"] in ("RESOURCE_NOT_FOUND", "INVALID_PATH")


def test_error_no_partial_mutation(tools):
    _setup_work_folder(tools, "test-partial")
    original_count = len(tools.fs_list("").get("entries", []))
    try:
        tools.fs_create("test-partial/_brief.md", "not valid")
    except Exception:
        pass
    current_count = len(tools.fs_list("").get("entries", []))
    assert current_count == original_count


# ── path governance tests ────────────────────────────────────────────────────

def test_path_traversal_write_rejected(tools):
    result = tools.fs_create("../outside/_brief.md", _brief_no_id("outside"))
    assert result["code"] == "INVALID_PATH"


def test_absolute_path_rejected(tools):
    result = tools.fs_create("/tmp/outside/_brief.md", _brief_no_id("outside"))
    assert result["code"] == "INVALID_PATH"


def test_dot_prefix_path_rejected(tools):
    result = tools.fs_create(".hidden/hidden/_brief.md", _brief_no_id("hidden"))
    assert result["code"] == "INVALID_PATH"


def test_excluded_dir_git_rejected(tools):
    result = tools.fs_create(".git/config.md", _brief_no_id("config"))
    assert result["code"] == "INVALID_PATH"


def test_excluded_dir_katana_rejected(tools):
    result = tools.fs_create(".katana/secret.md", _brief_no_id("secret"))
    assert result["code"] == "INVALID_PATH"


# ── resource-id-primary tests ────────────────────────────────────────────────

def test_delete_then_resolve_returns_resource_replaced(tools):
    _setup_work_folder(tools, "test-del-resolve")
    stat = tools.fs_stat("test-del-resolve/_brief.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-del-resolve/_brief.md")
    result = tools.fs_resolve(rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_write_after_delete_returns_resource_replaced(tools):
    _setup_work_folder(tools, "test-write-after-del")
    stat = tools.fs_stat("test-write-after-del/_brief.md")
    rid = stat["resource_id"]
    tools.fs_delete("test-write-after-del/_brief.md")
    content = _brief("test-write-after-del", rid=rid)
    result = tools.fs_write("test-write-after-del/_brief.md", content, resource_id=rid)
    assert result["code"] == "RESOURCE_REPLACED"


def test_batch_delete_then_resolve_returns_replaced(tools):
    _setup_work_folder(tools, "batch-del-resolve")
    stat = tools.fs_stat("batch-del-resolve/_brief.md")
    rid = stat["resource_id"]
    ops = [
        {"op": "fs_delete", "args": {"path": "batch-del-resolve/_brief.md"}},
    ]
    tools.fs_batch(ops)
    result = tools.fs_resolve(rid)
    assert result["code"] == "RESOURCE_REPLACED"


# ── MCP wrapper triggerability tests ─────────────────────────────────────────

def test_fs_create_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-create")
    result = _call(mcp, "fs_create", {
        "path": "mcp-create/_brief.md",
        "content": _brief_no_id("mcp-create"),
    })
    assert result["node_type"] == "file"
    assert result["resource_id"] is not None


def test_fs_read_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-read")
    result = _call(mcp, "fs_read", {"path": "mcp-read/_brief.md"})
    assert result["node_type"] == "file"
    assert result["content"] is not None


def test_fs_write_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-write")
    result = tools.fs_create("mcp-write/_brief.md", _brief_no_id("mcp-write"))
    rid = result["resource_id"]
    content = _brief("mcp-write-updated", rid=rid)
    result = _call(mcp, "fs_write", {
        "path": "mcp-write/_brief.md",
        "content": content,
    })
    assert result["node_type"] == "file"


def test_fs_delete_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-delete")
    result = _call(mcp, "fs_delete", {"path": "mcp-delete/_brief.md"})
    assert result["node_type"] == "file"


def test_fs_batch_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-batch-1")
    _setup_work_folder(tools, "mcp-batch-2")
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_create", "args": {"path": "mcp-batch-1/_brief.md", "content": _brief_no_id("mcp-batch-1")}},
            {"op": "fs_create", "args": {"path": "mcp-batch-2/_brief.md", "content": _brief_no_id("mcp-batch-2")}},
        ],
    })
    assert result["node_type"] == "batch"


def test_fs_resolve_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-resolve")
    result = _call(mcp, "fs_resolve", {"path_or_id": "mcp-resolve/_brief.md"})
    assert result["node_type"] == "file"


def test_fs_stat_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-stat")
    result = _call(mcp, "fs_stat", {"path": "mcp-stat/_brief.md"})
    assert result["node_type"] == "file"


def test_fs_list_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-list")
    result = _call(mcp, "fs_list", {})
    assert "entries" in result


def test_fs_glob_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-glob")
    result = _call(mcp, "fs_glob", {"pattern": "mcp-glob*/_brief.md"})
    assert result["node_type"] == "glob"


def test_fs_edit_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-edit")
    result = _call(mcp, "fs_edit", {
        "path": "mcp-edit/_brief.md",
        "old_string": "Test a work folder",
        "new_string": "Changed via MCP",
    })
    assert result["node_type"] == "file"


def test_fs_copy_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-copy-src")
    _setup_work_folder(tools, "mcp-copy-dst")
    result = _call(mcp, "fs_copy", {
        "source": "mcp-copy-src/_brief.md",
        "dest": "mcp-copy-dst/_brief.md",
    })
    assert result["node_type"] == "file"


def test_fs_rename_via_mcp(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-rename-src")
    _setup_work_folder(tools, "mcp-rename-dst")
    result = _call(mcp, "fs_rename", {
        "source": "mcp-rename-src/_brief.md",
        "dest": "mcp-rename-dst/_brief.md",
    })
    assert result["node_type"] == "file"


# ── content_revision / content_hash consistency ──────────────────────────────

def test_fs_read_content_hash_matches_content(tools):
    _setup_work_folder(tools, "test-hash")
    result = tools.fs_read("test-hash/_brief.md")
    assert result["content_hash"] == _hash(result["content"])


def test_fs_write_content_hash_matches_content(tools):
    _setup_work_folder(tools, "test-write-hash")
    result = tools.fs_create("test-write-hash/_brief.md", _brief_no_id("test-write-hash"))
    rid = result["resource_id"]
    content = _brief("test-write-hash-updated", rid=rid)
    result = tools.fs_write("test-write-hash/_brief.md", content)
    assert result["content_hash"] == _hash(result["content"])


def test_fs_create_content_hash_matches_content(tools):
    _setup_work_folder(tools, "test-create-hash")
    result = tools.fs_create("test-create-hash/_brief.md", _brief_no_id("test-create-hash"))
    assert result["content_hash"] == _hash(result["content"])


# ── _brief.md scan uses correct pattern ──────────────────────────────────────

def test_scan_uses_brief_pattern(tools):
    _setup_work_folder(tools, "test-scan")
    tools.fs_create("notes.md", "# Notes\n\ncontent")
    result = tools.fs_resolve("test-scan/_brief.md")
    assert result["resource_id"] is not None
    result = tools.fs_resolve("notes.md")
    assert result["resource_id"] is None


# ── MCP wrapper error-code triggerability ─────────────────────────────────────

def test_mcp_error_base_commit_conflict(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-cas")
    result = _call(mcp, "fs_write", {
        "path": "mcp-err-cas/_brief.md",
        "content": _brief("mcp-err-cas-updated", rid="wf-abc123"),
        "expected_base_commit": "0" * 40,
    })
    assert result["code"] == "BASE_COMMIT_CONFLICT"


def test_mcp_error_revision_conflict(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-rev")
    result = _call(mcp, "fs_write", {
        "path": "mcp-err-rev/_brief.md",
        "content": _brief("mcp-err-rev-updated", rid="wf-abc123"),
        "expected_resource_revision": "sha256:" + "0" * 64,
    })
    assert result["code"] == "REVISION_CONFLICT"


def test_mcp_error_idempotency_conflict(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-idem")
    key = "idem-key-mcp"
    tools.fs_create("mcp-err-idem/_brief.md", _brief_no_id("mcp-err-idem"), idempotency_key=key)
    _setup_work_folder(tools, "mcp-err-idem-2")
    result = _call(mcp, "fs_create", {
        "path": "mcp-err-idem-2/_brief.md",
        "content": _brief_no_id("mcp-err-idem-2"),
        "idempotency_key": key,
    })
    assert result["code"] == "IDEMPOTENCY_CONFLICT"


def test_mcp_error_resource_replaced(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-replaced")
    result = tools.fs_create("mcp-err-replaced/_brief.md", _brief_no_id("mcp-err-replaced"))
    rid = result["resource_id"]
    tools.fs_delete("mcp-err-replaced/_brief.md")
    result = _call(mcp, "fs_resolve", {"path_or_id": rid})
    assert result["code"] == "RESOURCE_REPLACED"


def test_mcp_error_ref_mismatch(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-ref")
    result = tools.fs_create("mcp-err-ref/_brief.md", _brief_no_id("mcp-err-ref"))
    rid = result["resource_id"]
    fake_id = "wf-999999"
    result = _call(mcp, "fs_write", {
        "path": "mcp-err-ref/_brief.md",
        "content": _brief("mcp-err-ref-updated", rid=fake_id),
        "resource_id": rid,
    })
    assert result["code"] == "REF_MISMATCH"


def test_mcp_error_policy_violation(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-policy")
    result = _call(mcp, "fs_write", {
        "path": "mcp-err-policy/progress.md",
        "content": "# Overwritten Progress\n\ndata",
    })
    assert result["code"] == "POLICY_VIOLATION"


def test_mcp_error_invalid_content(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-content")
    result = _call(mcp, "fs_create", {
        "path": "mcp-err-content/_brief.md",
        "content": "not a valid brief",
    })
    assert result["code"] == "INVALID_CONTENT"


def test_mcp_error_invalid_path(srv):
    mcp, repo, tools = srv
    result = _call(mcp, "fs_read", {"path": "../etc/passwd"})
    assert result["code"] == "INVALID_PATH"


def test_mcp_error_content_too_large(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-err-large")
    huge = "x" * 1_000_001
    result = _call(mcp, "fs_write", {
        "path": "mcp-err-large/_brief.md",
        "content": huge,
    })
    assert result["code"] == "CONTENT_TOO_LARGE"


def test_mcp_batch_error_ref_mismatch(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-batch-ref")
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_write", "args": {
                "path": "mcp-batch-ref/_brief.md",
                "content": _brief("mcp-batch-ref-updated", rid="wf-999999"),
            }},
        ],
    })
    assert result["code"] == "REF_MISMATCH"


def test_mcp_batch_error_content_too_large(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-batch-large")
    huge = "x" * 1_000_001
    result = _call(mcp, "fs_batch", {
        "operations": [
            {"op": "fs_write", "args": {
                "path": "mcp-batch-large/_brief.md",
                "content": huge,
            }},
        ],
    })
    assert result["code"] == "CONTENT_TOO_LARGE"


# ── MCP append-only error via MCP wrapper ────────────────────────────────────

def test_mcp_append_only_progress_violation(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-ao-progress")
    old_content = tools.fs_read("mcp-ao-progress/progress.md")["content"]
    new_content = old_content.replace("10:00:00", "99:99:99")
    result = _call(mcp, "fs_write", {
        "path": "mcp-ao-progress/progress.md",
        "content": new_content,
    })
    assert result["code"] == "POLICY_VIOLATION"


def test_mcp_append_only_golden_order_violation(srv):
    mcp, repo, tools = srv
    _setup_work_folder(tools, "mcp-ao-go")
    _create_file(tools, "mcp-ao-go/golden-order.md", "Rule 1: First rule\n")
    result = _call(mcp, "fs_write", {
        "path": "mcp-ao-go/golden-order.md",
        "content": "Rule 2: Different\n",
    })
    assert result["code"] == "POLICY_VIOLATION"


def test_mcp_resume_guide_violation(srv):
    mcp, repo, tools = srv
    _setup_work_folder_with_context(tools, "mcp-rg-violation")
    result = _call(mcp, "fs_write", {
        "path": "mcp-rg-violation/CLAUDE.md",
        "content": "# No resume guide\n\nJust content.\n",
    })
    assert result["code"] == "POLICY_VIOLATION"


def test_mcp_non_work_folder_brief_violation(srv):
    mcp, repo, tools = srv
    _mkdir(tools, "not-a-wf")
    result = _call(mcp, "fs_create", {
        "path": "not-a-wf/_brief.md",
        "content": _brief_no_id("not-a-wf"),
    })
    assert result["code"] == "POLICY_VIOLATION"