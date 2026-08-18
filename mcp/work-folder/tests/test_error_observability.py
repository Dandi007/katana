"""EK-3 error observability regression tests.

Covers the frozen contract:

- ``DirtyWorkTreeError`` is mapped to the ``WORKTREE_DIRTY`` envelope (never a
  generic ``OPERATION_FAILED``) by both ``fs_*`` and ``wf_append_progress``.
- ``_server_mutation`` fails closed as a controlled envelope for the two
  exceptions it now catches.
- every governed mutation emits exactly one structured ``governed_mutation``
  log line carrying ``op``/``mutation_id``/``commit_sha`` and, on failure, the
  root-cause ``error_type``/``error_code``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from katana_kernel import (
    CASRejectionError,
    DirtyWorkTreeError,
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
)
import katana_work_folder_mcp.server as server
from katana_work_folder_mcp.fs_tools import FSTools
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


def _now():
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
    (repo / ".gitignore").write_text("/.katana/runtime/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


@pytest.fixture
def env(tmp_path):
    _init_repo(tmp_path)
    kernel = GovernedKernel()
    vfs = GovernedVFS(str(tmp_path))
    ledger = ResourceIdLedger(
        str(tmp_path / ".katana" / "tombstones.json"),
        prefix="wf-",
    )
    runtime = tmp_path / ".katana" / "runtime"
    manifest = TransactionManifest(
        str(runtime / "manifests"),
        git_tracked=False,
    )
    mutation_ledger = SQLiteMutationLedger(str(runtime / "mutations.sqlite"))
    kernel.bind(
        "work-folder",
        _wf_policy(),
        vfs,
        ledger,
        manifest,
        str(tmp_path),
        mutation_ledger=mutation_ledger,
    )
    store = WorkFolderStore(kernel)
    tools = FSTools(kernel, str(tmp_path))
    folder_id = store.create("observability", _now)["folder_id"]
    return SimpleNamespace(
        repo=tmp_path,
        kernel=kernel,
        store=store,
        tools=tools,
        folder_id=folder_id,
    )


def _dirty_progress(repo: Path, folder_id: str) -> None:
    progress = repo / folder_id / "progress.md"
    progress.write_bytes(progress.read_bytes() + b"\ndirty progress\n")


def test_fs_edit_dirty_worktree_maps_to_worktree_dirty(env):
    env.tools.fs_create(env.folder_id, "notes.md", "hello world")
    _dirty_progress(env.repo, env.folder_id)

    result = env.tools.fs_edit(
        env.folder_id,
        "notes.md",
        "hello",
        "goodbye",
    )

    assert result["ok"] is False
    assert result["code"] == "WORKTREE_DIRTY"
    assert result["retryable"] is True
    assert env.folder_id in result["message"]


def test_append_progress_dirty_worktree_maps_to_worktree_dirty(env):
    _dirty_progress(env.repo, env.folder_id)

    result = env.store.append_progress(
        env.folder_id,
        "entry",
        "session-1",
        "key-dirty",
        now_fn=_now,
    )

    assert result["ok"] is False
    assert result["code"] == "WORKTREE_DIRTY"
    assert result["retryable"] is True
    assert env.folder_id in result["message"]


def test_server_mutation_catches_dirty_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))
    result = server._server_mutation(
        lambda: (_ for _ in ()).throw(
            DirtyWorkTreeError("governed mutation rejected within scope")
        )
    )
    assert result["code"] == "WORKTREE_DIRTY"
    assert result["retryable"] is True


def test_server_mutation_redacts_physical_path(tmp_path, monkeypatch):
    repo = os.path.realpath(str(tmp_path))
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))
    result = server._server_mutation(
        lambda: (_ for _ in ()).throw(
            DirtyWorkTreeError(f"cannot verify cleanliness: {repo}/wf-abc123")
        )
    )
    assert result["code"] == "WORKTREE_DIRTY"
    assert repo not in result["message"]
    assert "<work-folder-root>" in result["message"]


def test_server_mutation_catches_cas_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))
    result = server._server_mutation(
        lambda: (_ for _ in ()).throw(CASRejectionError("CAS mismatch"))
    )
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True


def test_configure_logging_installs_info_stderr_handler():
    server._configure_logging()

    logger = logging.getLogger("katana_work_folder_mcp")
    assert logger.level == logging.INFO
    assert any(
        isinstance(handler, logging.StreamHandler)
        and getattr(handler, "stream", None) is sys.stderr
        for handler in logger.handlers
    )


def test_failure_emits_structured_log_with_root_cause(env, caplog):
    caplog.set_level(logging.INFO, logger="katana_work_folder_mcp")
    _dirty_progress(env.repo, env.folder_id)

    result = env.store.append_progress(
        env.folder_id,
        "entry",
        "session-1",
        "key-log-fail",
        now_fn=_now,
    )

    assert result["code"] == "WORKTREE_DIRTY"
    assert any(
        record.message.startswith("governed_mutation")
        and "op=wf_append_progress" in record.message
        and "error_type=DirtyWorkTreeError" in record.message
        and "error_code=WORKTREE_DIRTY" in record.message
        for record in caplog.records
    )


def test_success_emits_structured_log_with_mutation_and_commit(env, caplog):
    caplog.set_level(logging.INFO, logger="katana_work_folder_mcp")

    result = env.store.append_progress(
        env.folder_id,
        "entry",
        "session-1",
        "key-log-success",
        now_fn=_now,
    )

    assert result["ok"] is True
    assert any(
        record.message.startswith("governed_mutation")
        and "op=wf_append_progress" in record.message
        and "mutation_id=" in record.message
        and "commit_sha=" in record.message
        and result.get("mutation_id") in record.message
        for record in caplog.records
    )