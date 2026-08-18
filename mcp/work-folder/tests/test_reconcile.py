"""Work Folder ``wf_reconcile`` tool exposure and recovery contract tests."""

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
)
from katana_work_folder_mcp import server
from katana_work_folder_mcp.fs_tools import FSTools
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


def _now():
    return datetime(2026, 8, 1, 10, 0, 0)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text("/.katana/runtime/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    kernel = GovernedKernel()
    vfs = GovernedVFS(str(tmp_path))
    ledger = ResourceIdLedger(
        str(tmp_path / ".katana" / "tombstones.json"), prefix="wf-",
    )
    runtime = tmp_path / ".katana" / "runtime"
    manifest = TransactionManifest(str(runtime / "manifests"), git_tracked=False)
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
    folder_id = store.create("primary", _now)["folder_id"]
    monkeypatch.setattr(server, "_kernel", kernel)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_fs_tools", tools)
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))
    return SimpleNamespace(
        repo=tmp_path,
        kernel=kernel,
        store=store,
        tools=tools,
        folder_id=folder_id,
    )


def _porcelain(repo) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def test_wf_reconcile_is_registered_tool(env):
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert "wf_reconcile" in names


def test_fs_capabilities_echoes_wf_reconcile(env):
    capabilities = env.tools.fs_capabilities()
    assert "wf_reconcile" in capabilities["capabilities"]["operations"]


def test_wf_reconcile_recovers_untracked_artifact_under_scope(env):
    scratch = env.repo / env.folder_id / "artifacts" / "report.log"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("generated", encoding="utf-8")
    assert "artifacts" in _porcelain(env.repo)

    result = asyncio.run(
        server.wf_reconcile(scope_prefixes=[env.folder_id]),
    )

    assert result["ok"] is True
    assert any(
        item.get("type") == "untracked_quarantined"
        for item in result["recovered"]
    )
    assert _porcelain(env.repo) == ""
    assert not scratch.exists()


def test_wf_reconcile_leaves_non_artifact_for_operator(env):
    scratch = env.repo / env.folder_id / "notes.md"
    scratch.write_text("primary content", encoding="utf-8")
    before = scratch.read_bytes()

    result = asyncio.run(
        server.wf_reconcile(scope_prefixes=[env.folder_id]),
    )

    assert result["ok"] is False
    assert result["code"] == "BROKEN"
    assert scratch.read_bytes() == before
    assert "notes.md" in _porcelain(env.repo)


def test_wf_reconcile_default_does_not_relocate_every_user_file(env):
    scratch = env.repo / "root-notes.md"
    scratch.write_text("ordinary file", encoding="utf-8")

    result = asyncio.run(server.wf_reconcile())

    assert result["ok"] is False
    assert result["code"] == "BROKEN"
    assert scratch.exists()


def test_wf_reconcile_idempotency_key_replays(env):
    scratch = env.repo / env.folder_id / "artifacts" / "report.log"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text("generated", encoding="utf-8")

    first = asyncio.run(
        server.wf_reconcile(
            scope_prefixes=[env.folder_id],
            idempotency_key="wf-reconcile-once",
        ),
    )
    assert first["ok"] is True

    second = asyncio.run(
        server.wf_reconcile(
            scope_prefixes=[env.folder_id],
            idempotency_key="wf-reconcile-once",
        ),
    )

    assert second == first
    assert not scratch.exists()


def test_wf_reconcile_broken_returns_structured_diagnosis_without_touching_tree(env):
    brief = env.repo / env.folder_id / "_brief.md"
    before = brief.read_bytes()
    brief.write_bytes(brief.read_bytes() + b"\nunattributable drift\n")

    result = asyncio.run(server.wf_reconcile())

    assert result["ok"] is False
    assert result["code"] == "BROKEN"
    assert result["manual_recovery_required"] is True
    assert "diagnostics" in result
    assert result["diagnostics"].get("suggested_commands")
    assert str(env.repo) not in str(result)
    assert brief.read_bytes() != before
    assert "_brief.md" in _porcelain(env.repo)