"""folder_id by-id addressing for fs_* writes (D1).

Root-cause fix for the double-nesting bug: agents can address a work folder
by its wf-xxxxxx id and write files by folder-relative logical path, without
ever deriving/learning the physical _wf_root layout. This makes the
"agent mis-judged VFS root and wrote to ghost nested path" failure class
structurally impossible.

See docs/specs/2026-07-15-wf-fs-path-contract-virtualization.md (D1, D3-A).
"""
import os
import subprocess

import pytest
from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    TransactionManifest,
)

from katana_work_folder_mcp.fs_tools import FSTools
from katana_work_folder_mcp.store import _wf_policy


def _brief_no_id(title: str) -> str:
    return f"""---
title: {title}
status: active
created: "2026-07-15"
updated: "2026-07-15"
tags: []
kind: ""
links: []
---
\n**Goal:** {title}

Summary.
"""


def _progress_md() -> str:
    return """# Progress

**Goal:** x
**Status:** active
**Phase:**
**Updated:** 2026-07-15

## Changelog
| Time | Action | Detail |
|------|--------|--------|
"""


@pytest.fixture
def tools(tmp_path):
    repo = str(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True, capture_output=True)
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json"), prefix="wf-")
    manifest = TransactionManifest(os.path.join(repo, ".katana", "manifests"))
    kernel.bind("work-folder", _wf_policy(), vfs, ledger, manifest, repo)
    return FSTools(kernel, repo)


def _seed_folder(tools, folder: str):
    """wf_create 等价: 建目录 + progress.md + commit, 然后 fs_create _brief 注册 id。"""
    os.makedirs(os.path.join(tools._repo_root, folder), exist_ok=True)
    tools._vfs.write(f"{folder}/progress.md", _progress_md())
    subprocess.run(["git", "add", "."], cwd=tools._repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=tools._repo_root, check=True, capture_output=True)
    return tools.fs_create(f"{folder}/_brief.md", _brief_no_id(folder.split("/")[-1]))["resource_id"]


def test_fs_create_by_folder_id_writes_inside_folder(tools):
    """folder_id + folder 内相对 path → 文件落在 folder 内, agent 无需知道物理路径"""
    fid = _seed_folder(tools, "2026/07/15/gw")
    assert fid and fid.startswith("wf-")
    r = tools.fs_create("design.md", "# Design\n", folder_id=fid)
    assert "code" not in r, f"unexpected error: {r}"
    assert tools._vfs.exists("2026/07/15/gw/design.md")


def test_fs_write_by_folder_id(tools):
    fid = _seed_folder(tools, "2026/07/15/gw")
    tools.fs_create("2026/07/15/gw/findings.md", "init\n")  # write 不隐式创建, 先建
    r = tools.fs_write("findings.md", "# Findings updated\n", folder_id=fid)
    assert "code" not in r, f"unexpected error: {r}"
    assert "Findings updated" in tools._vfs.read_text("2026/07/15/gw/findings.md")


def test_fs_create_folder_id_not_found(tools):
    r = tools.fs_create("design.md", "# x", folder_id="wf-deadbe")
    assert r["code"] == "RESOURCE_NOT_FOUND"


def test_folder_id_immune_to_double_nesting(tools):
    """folder_id + folder 内相对 path 永不产生 智元工作/工作记录/智元工作/工作记录/ 幽灵嵌套"""
    fid = _seed_folder(tools, "2026/07/15/gw")
    tools.fs_create("plan.md", "# Plan", folder_id=fid)
    assert tools._vfs.exists("2026/07/15/gw/plan.md")
    # 不应出现任何错位的物理 root 段嵌套
    assert not os.path.exists(os.path.join(tools._repo_root, "智元工作"))


def test_fs_create_without_folder_id_still_works(tools):
    """兼容: 不给 folder_id 时, 旧 path-based 用法 (含根目录孤立文件) 不受影响"""
    fid = _seed_folder(tools, "2026/07/15/gw")
    # 旧的 folder 内 path 写法仍工作
    r = tools.fs_create("2026/07/15/gw/notes.md", "# Notes\n")
    assert "code" not in r
    # 根目录孤立 md 也仍合法 (VFS 是通用 md FS, D2 不做 folder 归属硬校验)
    r2 = tools.fs_create("rootfile.md", "# Root\n")
    assert "code" not in r2
