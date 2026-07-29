"""Atomic, replay-safe ``wf_append_progress`` contract tests."""

from __future__ import annotations

import asyncio
import datetime
import inspect
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
    head_sha,
)
from katana_work_folder_mcp import server
from katana_work_folder_mcp.brief import parse_brief
from katana_work_folder_mcp.fs_tools import FSTools
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


FORBIDDEN = {
    "path",
    "folder",
    "wf_abs",
    "absolute_path",
    "changed_paths",
    "manifest",
    "resource_id",
    "virtual_path",
    "idempotency_key",
    "request_fingerprint",
}


def _at(day=30, minute=0):
    return lambda: datetime.datetime(2026, 7, day, 16, minute, 0)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Work Folder Test"], cwd=repo, check=True)
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text("/.katana/runtime/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


def _assert_safe(result, repo: Path) -> None:
    rendered = json.dumps(result, ensure_ascii=False, default=str)
    assert str(repo) not in rendered

    def walk(value):
        if isinstance(value, dict):
            assert FORBIDDEN.isdisjoint(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)


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
    folder_id = store.create("append progress", _at(29))["folder_id"]
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))
    monkeypatch.setattr(server, "_kernel", kernel)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_fs_tools", tools)
    monkeypatch.setattr(server, "_now", _at(30))
    return SimpleNamespace(
        repo=tmp_path,
        kernel=kernel,
        store=store,
        folder_id=folder_id,
    )


def _append(
    env,
    *,
    entry="完成 flat-storage 实现",
    source_session_id="session-001",
    idempotency_key="append-001",
    expected_base_sha=None,
    now_fn=None,
):
    return env.store.append_progress(
        env.folder_id,
        entry,
        source_session_id,
        idempotency_key,
        now_fn=now_fn or _at(30),
        expected_base_sha=expected_base_sha,
    )


def test_public_tool_signature_and_registration():
    assert list(inspect.signature(server.wf_append_progress).parameters) == [
        "folder_id",
        "entry",
        "source_session_id",
        "idempotency_key",
        "expected_base_sha",
    ]
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert "wf_append_progress" in names


def test_append_updates_progress_brief_and_index_in_one_commit(env):
    result = _append(env)
    progress = (env.repo / env.folder_id / "progress.md").read_text(encoding="utf-8")
    brief = parse_brief(
        (env.repo / env.folder_id / "_brief.md").read_text(encoding="utf-8")
    )
    index = (env.repo / "INDEX.md").read_text(encoding="utf-8")
    changed = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        cwd=env.repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert result["ok"] is True
    assert result["appended"] is True
    assert result["replayed"] is False
    assert result["folder_id"] == env.folder_id
    assert result["filename"] == "progress.md"
    assert result["source_session_id"] == "session-001"
    assert "session:session-001" in progress
    assert "完成 flat-storage 实现" in progress
    assert str(brief["frontmatter"]["updated"]) == "2026-07-30"
    assert env.folder_id in index
    assert {f"{env.folder_id}/progress.md", f"{env.folder_id}/_brief.md", "INDEX.md"} <= set(changed)
    assert not any(path.startswith(".katana/") for path in changed)


def test_append_reactivates_paused_brief(env):
    brief_path = env.repo / env.folder_id / "_brief.md"
    brief_path.write_text(
        brief_path.read_text(encoding="utf-8").replace(
            "status: active",
            "status: paused",
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=env.repo, check=True)
    subprocess.run(["git", "commit", "-qm", "pause"], cwd=env.repo, check=True)

    _append(env)

    brief = parse_brief(brief_path.read_text(encoding="utf-8"))
    assert brief["frontmatter"]["status"] == "active"


def test_exact_replay_returns_original_result_without_new_commit(env):
    first = _append(env)
    sha_after_first = head_sha(str(env.repo))
    second = _append(env, expected_base_sha="a" * 40)
    progress = (env.repo / env.folder_id / "progress.md").read_text(encoding="utf-8")

    assert second["ok"] is True
    assert second["appended"] is True
    assert second["replayed"] is True
    assert second["mutation_id"] == first["mutation_id"]
    assert second["commit"] == first["git"]["detail"]
    assert head_sha(str(env.repo)) == sha_after_first
    assert progress.count("完成 flat-storage 实现") == 1


@pytest.mark.parametrize(
    ("entry", "source_session_id"),
    [
        ("不同内容", "session-001"),
        ("完成 flat-storage 实现", "session-002"),
    ],
)
def test_same_key_with_different_payload_is_conflict(
    env,
    entry,
    source_session_id,
):
    _append(env)
    sha_before = head_sha(str(env.repo))

    conflict = _append(
        env,
        entry=entry,
        source_session_id=source_session_id,
    )

    assert conflict["ok"] is False
    assert conflict["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict["retryable"] is False
    assert head_sha(str(env.repo)) == sha_before


def test_idempotency_key_is_global_across_folders(env):
    _append(env)
    other_id = env.store.create("other", _at(30))["folder_id"]

    conflict = env.store.append_progress(
        other_id,
        "完成 flat-storage 实现",
        "session-001",
        "append-001",
        now_fn=_at(30),
    )

    assert conflict["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict["folder_id"] == other_id


def test_stale_cas_is_retryable_and_does_not_append(env):
    progress_before = (env.repo / env.folder_id / "progress.md").read_text(
        encoding="utf-8"
    )

    result = _append(env, expected_base_sha="a" * 40)

    assert result["ok"] is False
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True
    assert (env.repo / env.folder_id / "progress.md").read_text(
        encoding="utf-8"
    ) == progress_before


@pytest.mark.parametrize(
    ("entry", "source_session_id", "idempotency_key", "code"),
    [
        ("", "session", "key", "INVALID_CONTENT"),
        ("   ", "session", "key", "INVALID_CONTENT"),
        ("entry", "", "key", "INVALID_CONTENT"),
        ("entry", "session", "", "INVALID_CONTENT"),
        ("entry", "session", "x" * 257, "INVALID_CONTENT"),
    ],
)
def test_invalid_append_inputs_return_safe_error(
    env,
    entry,
    source_session_id,
    idempotency_key,
    code,
):
    result = env.store.append_progress(
        env.folder_id,
        entry,
        source_session_id,
        idempotency_key,
        now_fn=_at(30),
    )

    assert result["ok"] is False
    assert result["code"] == code
    _assert_safe(server._public_payload(result), env.repo)


def test_missing_and_noncanonical_folder_are_safe_errors(env):
    missing = env.store.append_progress(
        "wf-deadbe",
        "entry",
        "session",
        "missing-key",
        now_fn=_at(30),
    )
    invalid = env.store.append_progress(
        "../escape",
        "entry",
        "session",
        "invalid-key",
        now_fn=_at(30),
    )

    assert missing["code"] == "RESOURCE_NOT_FOUND"
    assert invalid["code"] == "INVALID_PATH"
    _assert_safe(server._public_payload(missing), env.repo)
    _assert_safe(server._public_payload(invalid), env.repo)


def test_missing_progress_is_rejected_without_partial_updates(env):
    progress = env.repo / env.folder_id / "progress.md"
    progress.unlink()
    subprocess.run(["git", "add", "."], cwd=env.repo, check=True)
    subprocess.run(["git", "commit", "-qm", "remove progress"], cwd=env.repo, check=True)
    brief_before = (env.repo / env.folder_id / "_brief.md").read_text(encoding="utf-8")
    index_before = (env.repo / "INDEX.md").read_text(encoding="utf-8")

    result = _append(env)

    assert result["code"] == "INVALID_CONTENT"
    assert not progress.exists()
    assert (env.repo / env.folder_id / "_brief.md").read_text(
        encoding="utf-8"
    ) == brief_before
    assert (env.repo / "INDEX.md").read_text(encoding="utf-8") == index_before


def test_multiline_and_table_delimiters_are_escaped_in_changelog(env):
    _append(env, entry="line one | value\nline two")
    progress = (env.repo / env.folder_id / "progress.md").read_text(encoding="utf-8")

    assert "line one \\| value<br>line two" in progress
    assert progress.count("session:session-001") == 1


def test_two_distinct_events_append_in_order(env):
    _append(env, entry="first", idempotency_key="key-1", now_fn=_at(30, 1))
    _append(env, entry="second", idempotency_key="key-2", now_fn=_at(30, 2))
    progress = (env.repo / env.folder_id / "progress.md").read_text(encoding="utf-8")

    assert progress.find("first") < progress.find("second")


def test_concurrent_same_key_has_one_commit_and_one_replay(env):
    sha_before = head_sha(str(env.repo))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _append(env), range(2)))
    commits = subprocess.run(
        ["git", "rev-list", "--count", f"{sha_before}..HEAD"],
        cwd=env.repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert sorted(result["replayed"] for result in results) == [False, True]
    assert commits == "1"
    progress = (env.repo / env.folder_id / "progress.md").read_text(encoding="utf-8")
    assert progress.count("完成 flat-storage 实现") == 1


def test_server_tool_returns_safe_public_envelope(env):
    result = asyncio.run(
        server.wf_append_progress(
            env.folder_id,
            "server append",
            "session-server",
            "server-key",
        )
    )

    assert result["ok"] is True
    assert result["folder_id"] == env.folder_id
    assert result["mutation_id"]
    assert result["commit"]
    _assert_safe(result, env.repo)
