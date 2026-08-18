"""Runtime 证据产物落点 + 仓内引用指针的回归测试（EK-4）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client

from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
    head_sha,
)
from katana_work_folder_mcp import server
from katana_work_folder_mcp.fs_tools import FSTools
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


FORBIDDEN_KEYS = {
    "path",
    "folder",
    "path_or_id",
    "resource_id",
    "virtual_path",
    "wf_abs",
    "absolute_path",
    "changed_paths",
    "manifest",
}


def _now():
    return datetime(2026, 7, 29, 16, 0, 0)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Work Folder Test"], cwd=repo, check=True)
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text("/.katana/runtime/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


def _assert_safe(payload, repo: Path) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, default=str)
    assert str(repo) not in rendered

    def walk(value):
        if isinstance(value, dict):
            assert FORBIDDEN_KEYS.isdisjoint(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)


def _tracked_paths(repo: Path) -> list[str]:
    return subprocess.run(
        ["git", "ls-files"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def _mcp_call(name: str, arguments: dict | None = None):
    async def call():
        async with Client(server.mcp) as client:
            return (await client.call_tool(name, arguments or {})).data

    return asyncio.run(call())


@pytest.fixture
def env(tmp_path, monkeypatch):
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
    folder_id = store.create("evidence", _now)["folder_id"]
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


def test_evidence_put_lands_in_runtime_and_leaves_hash_reference(env):
    content = "# audit-evidence-r16\n" + ("x" * 4096)
    result = env.tools.wf_evidence_put(
        env.folder_id,
        "audit-evidence-r16.md",
        content,
    )

    runtime_rel = f".katana/runtime/evidence/{env.folder_id}/audit-evidence-r16.md"
    runtime_abs = env.repo / runtime_rel
    ref_abs = env.repo / env.folder_id / result["reference_filename"]

    assert result["ok"] is True
    assert result["folder_id"] == env.folder_id
    assert result["filename"] == "audit-evidence-r16.md"
    assert result["runtime_path"] == runtime_rel
    assert result["sha256"] == "sha256:" + hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()

    # 原文落在 runtime root，未进 git 事务。
    assert runtime_abs.read_bytes() == content.encode("utf-8")
    assert runtime_rel not in _tracked_paths(env.repo)

    # 引用文件进仓且留指针，体积收敛到指针级。
    assert ref_abs.is_file()
    ref_text = ref_abs.read_text(encoding="utf-8")
    assert f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}" in ref_text
    assert f"filename: audit-evidence-r16.md" in ref_text
    assert f"runtime_path: {runtime_rel}" in ref_text
    assert len(ref_text.encode("utf-8")) < len(content.encode("utf-8"))
    assert f"{env.folder_id}/{result['reference_filename']}" in _tracked_paths(env.repo)

    # 引用完整性：folder 内引用文件的 sha256 可复算出 runtime 产物 hash。
    assert hashlib.sha256(runtime_abs.read_bytes()).hexdigest() == result[
        "sha256"
    ].removeprefix("sha256:")
    _assert_safe(result, env.repo)


def test_evidence_put_is_idempotent(env):
    first = env.tools.wf_evidence_put(
        env.folder_id,
        "audit-evidence-r17.md",
        "stable-evidence\n",
        idempotency_key="evidence-key",
    )
    head_after_first = head_sha(str(env.repo))
    replay = env.tools.wf_evidence_put(
        env.folder_id,
        "audit-evidence-r17.md",
        "stable-evidence\n",
        idempotency_key="evidence-key",
    )

    assert first["ok"] is True
    assert replay == first
    assert head_sha(str(env.repo)) == head_after_first


def test_evidence_put_rejects_invalid_inputs(env):
    bad_folder = env.tools.wf_evidence_put(
        "not-an-id",
        "audit-evidence-r18.md",
        "x",
    )
    bad_filename = env.tools.wf_evidence_put(
        env.folder_id,
        "../audit-evidence-r18.md",
        "x",
    )
    empty = env.tools.wf_evidence_put(
        env.folder_id,
        "audit-evidence-r18.md",
        "",
    )
    lifecycle = env.tools.wf_evidence_put(
        env.folder_id,
        "_brief.md",
        "x",
    )

    assert bad_folder["code"] == "INVALID_PATH"
    assert bad_filename["code"] == "INVALID_PATH"
    assert empty["code"] == "INVALID_CONTENT"
    assert lifecycle["code"] == "POLICY_VIOLATION"


def test_evidence_migrate_moves_existing_files_and_recomputes_hash(env):
    env.tools.fs_create(env.folder_id, "audit-evidence-r15.md", "legacy evidence\n")

    result = env.tools.wf_evidence_migrate(env.folder_id)

    assert result["ok"] is True
    assert len(result["migrated"]) == 1
    migrated = result["migrated"][0]
    assert migrated["filename"] == "audit-evidence-r15.md"
    runtime_rel = migrated["runtime_path"]
    assert runtime_rel == (
        f".katana/runtime/evidence/{env.folder_id}/audit-evidence-r15.md"
    )

    assert not (env.repo / env.folder_id / "audit-evidence-r15.md").exists()
    assert (env.repo / runtime_rel).read_text(encoding="utf-8") == "legacy evidence\n"
    ref_abs = env.repo / env.folder_id / migrated["reference_filename"]
    assert ref_abs.is_file()
    assert hashlib.sha256((env.repo / runtime_rel).read_bytes()).hexdigest() == (
        migrated["sha256"].removeprefix("sha256:")
    )

    tracked = _tracked_paths(env.repo)
    assert runtime_rel not in tracked
    assert f"{env.folder_id}/audit-evidence-r15.md" not in tracked
    assert f"{env.folder_id}/{migrated['reference_filename']}" in tracked
    _assert_safe(result, env.repo)


def test_evidence_migrate_is_idempotent(env):
    env.tools.fs_create(env.folder_id, "audit-evidence-r13.md", "idem migrate\n")

    first = env.tools.wf_evidence_migrate(
        env.folder_id,
        idempotency_key="migrate-key",
    )
    head_after_first = head_sha(str(env.repo))
    replay = env.tools.wf_evidence_migrate(
        env.folder_id,
        idempotency_key="migrate-key",
    )

    assert first["ok"] is True
    assert replay["migrated"] == first["migrated"]
    assert head_sha(str(env.repo)) == head_after_first


def test_evidence_migrate_dry_run_is_read_only(env):
    env.tools.fs_create(env.folder_id, "audit-evidence-r14.md", "pre-migration\n")
    sha_before = head_sha(str(env.repo))

    result = env.tools.wf_evidence_migrate(env.folder_id, dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["migrated"][0]["filename"] == "audit-evidence-r14.md"
    assert (env.repo / env.folder_id / "audit-evidence-r14.md").exists()
    assert head_sha(str(env.repo)) == sha_before


def test_evidence_tools_are_triggerable_through_mcp(env):
    put = _mcp_call(
        "wf_evidence_put",
        {
            "folder_id": env.folder_id,
            "filename": "audit-evidence-r19.md",
            "content": "mcp evidence\n",
        },
    )
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}

    assert put["ok"] is True
    assert put["filename"] == "audit-evidence-r19.md"
    assert "wf_evidence_put" in names
    assert "wf_evidence_migrate" in names
    _assert_safe(put, env.repo)