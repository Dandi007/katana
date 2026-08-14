"""Scope-aware clean guard integration: sister-folder dirt must not block.

Work-folder folder-level mutations pass scope_prefixes=[folder] plus control
paths (INDEX.md) to GovernedKernel.mutate.  Dirt in a sibling folder is
preserved byte-for-byte and never leaks into the committed diff, while dirt
inside the folder itself or on governance control paths still fails closed.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from katana_kernel import (
    DirtyWorkTreeError,
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
)
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


def _fixed_now():
    return datetime(2026, 7, 29, 16, 0, 0)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Work Folder Test"],
        cwd=repo,
        check=True,
    )
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text(
        "/.katana/runtime/\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", ".gitkeep", ".gitignore"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


def _bind(repo: Path):
    kernel = GovernedKernel()
    vfs = GovernedVFS(str(repo))
    ledger = ResourceIdLedger(
        str(repo / ".katana" / "tombstones.json"),
        prefix="wf-",
    )
    runtime = repo / ".katana" / "runtime"
    manifest = TransactionManifest(
        str(runtime / "manifests"),
        git_tracked=False,
    )
    kernel.bind(
        "work-folder",
        _wf_policy(),
        vfs,
        ledger,
        manifest,
        str(repo),
        mutation_ledger=SQLiteMutationLedger(runtime / "mutations.sqlite"),
    )
    return kernel, WorkFolderStore(kernel)


@pytest.fixture
def repo_store(tmp_path):
    _init_repo(tmp_path)
    kernel, store = _bind(tmp_path)
    return tmp_path, kernel, store


def _dirty_folder(repo: Path, folder_id: str) -> tuple[bytes, bytes]:
    tracked = repo / folder_id / "progress.md"
    tracked.write_bytes(tracked.read_bytes() + b"\ndirty edit\n")
    untracked = repo / folder_id / "scratch.txt"
    untracked.write_text("scratch\n", encoding="utf-8")
    return tracked.read_bytes(), untracked.read_bytes()


def test_append_progress_succeeds_with_dirty_sister_folder(repo_store):
    repo, _, store = repo_store
    folder_a = store.create("alpha", _fixed_now)["folder_id"]
    folder_b = store.create("beta", _fixed_now)["folder_id"]

    b_tracked_before, b_untracked_before = _dirty_folder(repo, folder_b)

    result = store.append_progress(
        folder_a,
        "推进记录",
        "session-1",
        "key-abc",
        now_fn=_fixed_now,
    )

    assert result["ok"] is True
    assert result["appended"] is True
    assert (repo / folder_b / "progress.md").read_bytes() == b_tracked_before
    assert (repo / folder_b / "scratch.txt").read_bytes() == b_untracked_before

    sha = result["git"]["detail"]
    committed = subprocess.run(
        [
            "git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha,
        ],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert not any(path.startswith(f"{folder_b}/") for path in committed)
    assert any(path.startswith(f"{folder_a}/") for path in committed)


def test_append_progress_rejected_when_folder_itself_dirty(repo_store):
    repo, _, store = repo_store
    folder_a = store.create("alpha", _fixed_now)["folder_id"]
    _dirty_folder(repo, folder_a)

    with pytest.raises(DirtyWorkTreeError):
        store.append_progress(
            folder_a,
            "entry",
            "session-1",
            "key-dirty",
            now_fn=_fixed_now,
        )


def test_append_progress_rejected_when_control_index_dirty(repo_store):
    repo, _, store = repo_store
    folder_a = store.create("alpha", _fixed_now)["folder_id"]
    index = repo / "INDEX.md"
    index_before = index.read_bytes()
    index.write_bytes(index_before + b"\nstale index\n")

    with pytest.raises(DirtyWorkTreeError):
        store.append_progress(
            folder_a,
            "entry",
            "session-1",
            "key-index",
            now_fn=_fixed_now,
        )


def test_create_succeeds_with_dirty_sister_folder(repo_store):
    """建新 folder 不该被别人 folder 里的脏改动阻断。

    实测过的真实故障：某条工作线把运行时产物（trace snapshot、日志、
    __pycache__）写进自己的 work folder，导致**所有** session 的 wf_create
    被整仓 clean 前置条件拒绝。create 触及的既有路径只有 INDEX.md——
    新 folder 由 mint 循环保证不存在，天然干净。
    """
    repo, _, store = repo_store
    folder_b = store.create("beta", _fixed_now)["folder_id"]
    b_tracked_before, b_untracked_before = _dirty_folder(repo, folder_b)

    result = store.create("gamma", _fixed_now)

    assert result["created"] is True
    new_folder = result["folder_id"]
    assert new_folder != folder_b

    # 兄弟 folder 的脏内容逐字节保留，且不泄进本次提交
    assert (repo / folder_b / "progress.md").read_bytes() == b_tracked_before
    assert (repo / folder_b / "scratch.txt").read_bytes() == b_untracked_before

    sha = result["git"]["detail"]
    committed = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert not any(path.startswith(f"{folder_b}/") for path in committed)
    assert any(path.startswith(f"{new_folder}/") for path in committed)


def test_create_rejected_when_control_index_dirty(repo_store):
    """收窄不等于放开：控制面（INDEX.md）脏时 create 仍须 fail closed。"""
    repo, _, store = repo_store
    store.create("alpha", _fixed_now)
    index = repo / "INDEX.md"
    index.write_bytes(index.read_bytes() + b"\nstale index\n")

    with pytest.raises(DirtyWorkTreeError):
        store.create("delta", _fixed_now)
