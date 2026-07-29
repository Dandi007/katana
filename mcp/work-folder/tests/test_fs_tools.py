"""ID-only Full VFS contract and governance tests."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
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
    MutationBrokenError,
    ResourceIdLedger,
    TransactionManifest,
    head_sha,
)
from katana_work_folder_mcp import server
from katana_work_folder_mcp.brief import parse_brief, render_brief
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


def _sha(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Work Folder Test"], cwd=repo, check=True)
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, check=True)
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
    manifest = TransactionManifest(str(tmp_path / ".katana" / "manifests"))
    kernel.bind("work-folder", _wf_policy(), vfs, ledger, manifest, str(tmp_path))
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


def _add_folder(env, topic="secondary") -> str:
    return env.store.create(topic, _now)["folder_id"]


# Discovery -----------------------------------------------------------------


def test_capabilities_are_id_only_and_delete_glob(env):
    result = env.tools.fs_capabilities()
    operations = result["capabilities"]["operations"]

    assert result["ok"] is True
    assert result["capabilities"]["addressing"] == (
        "folder_id + folder-relative filename"
    )
    assert "fs_glob" not in operations
    assert not hasattr(FSTools, "fs_glob")
    _assert_safe(result, env.repo)


def test_resolve_defaults_to_brief(env):
    result = env.tools.fs_resolve(env.folder_id)

    assert result["ok"] is True
    assert result["folder_id"] == env.folder_id
    assert result["filename"] == "_brief.md"
    assert result["node_type"] == "file"
    assert result["content_revision"].startswith("sha256:")
    _assert_safe(result, env.repo)


def test_stat_file_and_directory(env):
    env.tools.fs_create(env.folder_id, "notes/a.md", "A\n")

    file_result = env.tools.fs_stat(env.folder_id, "notes/a.md")
    dir_result = env.tools.fs_stat(env.folder_id, "notes")

    assert file_result["node_type"] == "file"
    assert file_result["size"] == 2
    assert dir_result["node_type"] == "directory"
    assert "size" not in dir_result


def test_list_root_and_nested_directory(env):
    env.tools.fs_create(env.folder_id, "notes/a.md", "A\n")
    env.tools.fs_create(env.folder_id, "notes/b.md", "B\n")

    root = env.tools.fs_list(env.folder_id)
    nested = env.tools.fs_list(env.folder_id, "notes")

    assert root["node_type"] == "directory"
    root_names = {entry["filename"] for entry in root["entries"]}
    assert {"_brief.md", "context.md", "progress.md", "notes"} <= root_names
    notes = {entry["filename"] for entry in nested["entries"]}
    assert notes == {"notes/a.md", "notes/b.md"}
    assert all("path" not in entry for entry in root["entries"] + nested["entries"])


def test_read_returns_numbered_slice_and_raw_revision(env):
    env.tools.fs_create(env.folder_id, "lines.md", "one\ntwo\nthree\n")

    result = env.tools.fs_read(env.folder_id, "lines.md", offset=2, limit=1)

    assert result["content"] == "2\ttwo"
    assert result["content_revision"] == _sha("one\ntwo\nthree\n")


@pytest.mark.parametrize(
    ("folder_id", "filename", "code"),
    [
        ("wf-deadbe", "notes.md", "RESOURCE_NOT_FOUND"),
        ("not-an-id", "notes.md", "INVALID_PATH"),
        ("../wf-abc123", "notes.md", "INVALID_PATH"),
        ("wf-abc123", "../escape.md", "INVALID_PATH"),
        ("wf-abc123", "/absolute.md", "INVALID_PATH"),
        ("wf-abc123", ".git/config", "INVALID_PATH"),
        ("wf-abc123", ".katana/secret", "INVALID_PATH"),
        ("wf-abc123", "nested/.hidden", "INVALID_PATH"),
    ],
)
def test_discovery_rejects_missing_or_unsafe_locator(env, folder_id, filename, code):
    if folder_id == "wf-abc123":
        folder_id = env.folder_id

    result = env.tools.fs_read(folder_id, filename)

    assert result["ok"] is False
    assert result["code"] == code
    _assert_safe(result, env.repo)


def test_folder_with_missing_or_mismatched_brief_is_invalid(env):
    missing = env.repo / "wf-deadbe"
    missing.mkdir()
    no_brief = env.tools.fs_read("wf-deadbe", "notes.md")

    mismatch_id = _add_folder(env)
    brief = env.repo / mismatch_id / "_brief.md"
    brief.write_text(
        brief.read_text(encoding="utf-8").replace(mismatch_id, "wf-deadbe", 1),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=env.repo, check=True)
    subprocess.run(["git", "commit", "-qm", "mismatch"], cwd=env.repo, check=True)
    mismatch = env.tools.fs_read(mismatch_id, "progress.md")

    assert no_brief["code"] == "INVALID_CONTENT"
    assert mismatch["code"] == "INVALID_CONTENT"


def test_tombstoned_folder_id_is_resource_replaced(env):
    env.kernel.get_binding("work-folder").ledger.tombstone(env.folder_id)

    result = env.tools.fs_resolve(env.folder_id)

    assert result["code"] == "RESOURCE_REPLACED"
    assert result["folder_id"] == env.folder_id


def test_discovery_not_found_variants_and_symlink_are_safe(env):
    missing_resolve = env.tools.fs_resolve(env.folder_id, "missing.md")
    missing_stat = env.tools.fs_stat(env.folder_id, "missing.md")
    missing_list = env.tools.fs_list(env.folder_id, "missing")
    target = env.repo / env.folder_id / "target.md"
    target.write_text("target", encoding="utf-8")
    link = env.repo / env.folder_id / "link.md"
    link.symlink_to(target)
    symlink = env.tools.fs_read(env.folder_id, "link.md")

    assert missing_resolve["code"] == "RESOURCE_NOT_FOUND"
    assert missing_stat["code"] == "RESOURCE_NOT_FOUND"
    assert missing_list["code"] == "RESOURCE_NOT_FOUND"
    assert symlink["code"] == "INVALID_PATH"
    for result in (missing_resolve, missing_stat, missing_list, symlink):
        _assert_safe(result, env.repo)


# Single-file mutations ------------------------------------------------------


def test_create_write_edit_delete_roundtrip(env):
    created = env.tools.fs_create(env.folder_id, "notes.md", "alpha\n")
    written = env.tools.fs_write(env.folder_id, "notes.md", "beta\n")
    edited = env.tools.fs_edit(env.folder_id, "notes.md", "beta", "gamma")
    deleted = env.tools.fs_delete(env.folder_id, "notes.md")

    for result in (created, written, edited, deleted):
        assert result["ok"] is True
        assert result["folder_id"] == env.folder_id
        assert result["filename"] == "notes.md"
        assert result["commit"]
        assert result["mutation_id"]
        _assert_safe(result, env.repo)
    assert edited["content"] == "gamma\n"
    assert not (env.repo / env.folder_id / "notes.md").exists()


def test_create_rejects_duplicate_empty_too_large_and_lifecycle_files(env):
    env.tools.fs_create(env.folder_id, "notes.md", "first")

    duplicate = env.tools.fs_create(env.folder_id, "notes.md", "second")
    empty = env.tools.fs_create(env.folder_id, "empty.md", "")
    large = env.tools.fs_create(env.folder_id, "large.md", "x" * 1_000_001)
    brief = env.tools.fs_create(env.folder_id, "_brief.md", "x")
    progress = env.tools.fs_create(env.folder_id, "progress.md", "x")

    assert duplicate["code"] == "RESOURCE_EXISTS"
    assert empty["code"] == "POLICY_VIOLATION"
    assert large["code"] == "CONTENT_TOO_LARGE"
    assert brief["code"] == "POLICY_VIOLATION"
    assert progress["code"] == "POLICY_VIOLATION"


def test_write_never_implicitly_creates(env):
    result = env.tools.fs_write(env.folder_id, "missing.md", "content")

    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert not (env.repo / env.folder_id / "missing.md").exists()


def test_edit_requires_exact_or_explicit_replace_all(env):
    env.tools.fs_create(env.folder_id, "notes.md", "x x")

    ambiguous = env.tools.fs_edit(env.folder_id, "notes.md", "x", "y")
    all_result = env.tools.fs_edit(
        env.folder_id,
        "notes.md",
        "x",
        "y",
        replace_all=True,
    )
    absent = env.tools.fs_edit(env.folder_id, "notes.md", "z", "w")

    assert ambiguous["code"] == "INVALID_CONTENT"
    assert all_result["content"] == "y y"
    assert absent["code"] == "INVALID_CONTENT"


def test_revision_cas_and_idempotency_conflicts_are_machine_readable(env):
    created = env.tools.fs_create(
        env.folder_id,
        "notes.md",
        "v1",
        idempotency_key="create-notes",
    )
    stale_revision = env.tools.fs_write(
        env.folder_id,
        "notes.md",
        "v2",
        expected_resource_revision="sha256:" + "0" * 64,
    )
    stale_base = env.tools.fs_write(
        env.folder_id,
        "notes.md",
        "v2",
        expected_base_commit="a" * 40,
    )
    replay = env.tools.fs_create(
        env.folder_id,
        "other.md",
        "content",
        idempotency_key="create-notes",
    )

    assert created["ok"] is True
    assert stale_revision["code"] == "REVISION_CONFLICT"
    assert stale_revision["retryable"] is True
    assert stale_base["code"] == "BASE_COMMIT_CONFLICT"
    assert stale_base["retryable"] is True
    assert replay["code"] == "IDEMPOTENCY_CONFLICT"


def test_matching_revision_allows_write(env):
    created = env.tools.fs_create(env.folder_id, "notes.md", "v1")

    result = env.tools.fs_write(
        env.folder_id,
        "notes.md",
        "v2",
        expected_resource_revision=created["content_revision"],
    )

    assert result["ok"] is True
    assert result["content"] == "v2"
    assert result["content_revision"] == _sha("v2")


def test_brief_id_is_immutable_but_semantic_update_is_allowed(env):
    brief_path = env.repo / env.folder_id / "_brief.md"
    parsed = parse_brief(brief_path.read_text(encoding="utf-8"))
    fm = parsed["frontmatter"]
    valid = render_brief(
        id=env.folder_id,
        title=fm["title"],
        status="paused",
        created=str(fm["created"]),
        updated="2026-07-30",
        goal=parsed["goal"],
        summary=parsed["summary"],
    )
    invalid = valid.replace(env.folder_id, "wf-deadbe", 1)

    accepted = env.tools.fs_write(env.folder_id, "_brief.md", valid)
    rejected = env.tools.fs_write(env.folder_id, "_brief.md", invalid)

    assert accepted["ok"] is True
    assert rejected["code"] == "INVALID_CONTENT"
    assert "immutable" in rejected["message"]


def test_progress_changelog_and_blocked_section_are_conserved(env):
    progress_path = env.repo / env.folder_id / "progress.md"
    old = progress_path.read_text(encoding="utf-8")
    appended = old + "| 16:01 | progress | done |\n"

    accepted = env.tools.fs_write(env.folder_id, "progress.md", appended)
    rewrite = env.tools.fs_write(
        env.folder_id,
        "progress.md",
        appended.replace("| 16:01 | progress | done |\n", ""),
    )
    remove_blocked = env.tools.fs_write(
        env.folder_id,
        "progress.md",
        appended.replace("## Blocked\n- None\n\n", ""),
    )

    assert accepted["ok"] is True
    assert rewrite["code"] == "POLICY_VIOLATION"
    assert remove_blocked["code"] == "POLICY_VIOLATION"


def test_edit_preserves_brief_and_append_only_documents(env):
    brief_path = env.repo / env.folder_id / "_brief.md"
    brief = brief_path.read_text(encoding="utf-8")
    identity_edit = env.tools.fs_edit(
        env.folder_id,
        "_brief.md",
        env.folder_id,
        "wf-deadbe",
    )

    progress_path = env.repo / env.folder_id / "progress.md"
    progress = progress_path.read_text(encoding="utf-8")
    progress_append = env.tools.fs_edit(
        env.folder_id,
        "progress.md",
        progress,
        progress + "| 16:01 | edit | appended |\n",
    )

    env.store.save(
        env.folder_id,
        _now,
        golden_order_additions="- original\n",
    )
    golden_append = env.tools.fs_edit(
        env.folder_id,
        "golden-order.md",
        "- original\n",
        "- original\n- appended\n",
    )

    assert brief_path.read_text(encoding="utf-8") == brief
    assert identity_edit["code"] == "INVALID_CONTENT"
    assert progress_append["ok"] is True
    assert golden_append["ok"] is True


def test_golden_order_is_append_only(env):
    env.store.save(
        env.folder_id,
        _now,
        golden_order_additions="- original\n",
    )
    path = env.repo / env.folder_id / "golden-order.md"
    old = path.read_text(encoding="utf-8")

    accepted = env.tools.fs_write(
        env.folder_id,
        "golden-order.md",
        old + "- appended\n",
    )
    rejected = env.tools.fs_write(
        env.folder_id,
        "golden-order.md",
        "- replaced\n",
    )

    assert accepted["ok"] is True
    assert rejected["code"] == "POLICY_VIOLATION"


@pytest.mark.parametrize(
    ("filename", "heading"),
    [
        ("context.md", "## 关键路径"),
        ("CLAUDE.md", "## Resume Steps"),
        ("AGENTS.md", "## Goal"),
    ],
)
def test_governed_document_sections_are_conserved(env, filename, heading):
    if filename != "context.md":
        env.store.save(env.folder_id, _now)
    path = env.repo / env.folder_id / filename
    old = path.read_text(encoding="utf-8")
    replacement = old.replace(heading, f"## Removed {heading}")

    result = env.tools.fs_write(env.folder_id, filename, replacement)

    assert result["code"] == "POLICY_VIOLATION"


def test_governed_documents_allow_updates_that_preserve_sections(env):
    env.store.save(env.folder_id, _now)
    for filename in ("context.md", "CLAUDE.md", "AGENTS.md"):
        path = env.repo / env.folder_id / filename
        old = path.read_text(encoding="utf-8")
        result = env.tools.fs_write(
            env.folder_id,
            filename,
            old + "\n补充上下文。\n",
        )
        assert result["ok"] is True


@pytest.mark.parametrize("filename", ["_brief.md", "progress.md", "golden-order.md"])
def test_delete_rejects_identity_and_critical_files(env, filename):
    if filename == "golden-order.md":
        env.store.save(
            env.folder_id,
            _now,
            golden_order_additions="- decision\n",
        )

    result = env.tools.fs_delete(env.folder_id, filename)

    assert result["code"] == "POLICY_VIOLATION"


def test_mutation_broken_error_is_sanitized(env, monkeypatch):
    monkeypatch.setattr(
        env.kernel,
        "mutate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            MutationBrokenError(
                f"recovery evidence at {env.repo}/secret",
                {"state": "BROKEN", "paths": [str(env.repo / "secret")]},
            )
        ),
    )

    result = env.tools.fs_create(env.folder_id, "notes.md", "content")

    assert result["code"] == "BROKEN"
    _assert_safe(result, env.repo)


# Cross-folder transfer ------------------------------------------------------


def test_copy_and_rename_across_distinct_folder_ids(env):
    dest_id = _add_folder(env)
    env.tools.fs_create(env.folder_id, "source.md", "payload")

    copied = env.tools.fs_copy(
        env.folder_id,
        "source.md",
        dest_id,
        "copied.md",
    )
    renamed = env.tools.fs_rename(
        env.folder_id,
        "source.md",
        dest_id,
        "renamed.md",
    )

    assert copied["folder_id"] == dest_id
    assert copied["filename"] == "copied.md"
    assert renamed["folder_id"] == dest_id
    assert renamed["filename"] == "renamed.md"
    assert (env.repo / dest_id / "copied.md").read_text(encoding="utf-8") == "payload"
    assert (env.repo / dest_id / "renamed.md").read_text(encoding="utf-8") == "payload"
    assert not (env.repo / env.folder_id / "source.md").exists()


def test_transfer_rejects_missing_existing_and_critical_files(env):
    dest_id = _add_folder(env)
    env.tools.fs_create(env.folder_id, "source.md", "payload")
    env.tools.fs_create(dest_id, "exists.md", "dest")

    missing = env.tools.fs_copy(
        env.folder_id,
        "missing.md",
        dest_id,
        "new.md",
    )
    exists = env.tools.fs_copy(
        env.folder_id,
        "source.md",
        dest_id,
        "exists.md",
    )
    critical = env.tools.fs_copy(
        env.folder_id,
        "progress.md",
        dest_id,
        "progress-copy.md",
    )
    brief = env.tools.fs_rename(
        env.folder_id,
        "_brief.md",
        dest_id,
        "brief.md",
    )

    assert missing["code"] == "RESOURCE_NOT_FOUND"
    assert exists["code"] == "RESOURCE_EXISTS"
    assert critical["code"] == "POLICY_VIOLATION"
    assert brief["code"] == "POLICY_VIOLATION"


def test_transfer_rejects_source_and_destination_traversal(env):
    dest_id = _add_folder(env)
    env.tools.fs_create(env.folder_id, "source.md", "payload")

    bad_source = env.tools.fs_copy(
        env.folder_id,
        "../source.md",
        dest_id,
        "copy.md",
    )
    bad_dest = env.tools.fs_rename(
        env.folder_id,
        "source.md",
        dest_id,
        "../rename.md",
    )

    assert bad_source["code"] == "INVALID_PATH"
    assert bad_dest["code"] == "INVALID_PATH"
    assert (env.repo / env.folder_id / "source.md").exists()


def test_delete_missing_file_is_not_found(env):
    result = env.tools.fs_delete(env.folder_id, "missing.md")

    assert result["code"] == "RESOURCE_NOT_FOUND"


# Batch ---------------------------------------------------------------------


def _op(op: str, **args) -> dict:
    return {"op": op, "args": args}


def test_batch_create_write_edit_delete_and_safe_results(env):
    env.tools.fs_create(env.folder_id, "write.md", "old")
    env.tools.fs_create(env.folder_id, "edit.md", "before")
    env.tools.fs_create(env.folder_id, "delete.md", "bye")

    result = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="created.md",
                content="new",
            ),
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="write.md",
                content="updated",
            ),
            _op(
                "fs_edit",
                folder_id=env.folder_id,
                filename="edit.md",
                old_string="before",
                new_string="after",
            ),
            _op(
                "fs_delete",
                folder_id=env.folder_id,
                filename="delete.md",
            ),
        ],
        idempotency_key="batch-one",
    )

    assert result["ok"] is True
    assert result["node_type"] == "batch"
    assert [item["op"] for item in result["batch_results"]] == [
        "fs_create",
        "fs_write",
        "fs_edit",
        "fs_delete",
    ]
    assert (env.repo / env.folder_id / "created.md").read_text() == "new"
    assert (env.repo / env.folder_id / "write.md").read_text() == "updated"
    assert (env.repo / env.folder_id / "edit.md").read_text() == "after"
    assert not (env.repo / env.folder_id / "delete.md").exists()
    _assert_safe(result, env.repo)


def test_batch_cross_folder_copy_and_rename(env):
    dest_id = _add_folder(env)
    env.tools.fs_create(env.folder_id, "copy.md", "copy")
    env.tools.fs_create(env.folder_id, "rename.md", "rename")

    result = env.tools.fs_batch(
        [
            _op(
                "fs_copy",
                source_folder_id=env.folder_id,
                source_filename="copy.md",
                dest_folder_id=dest_id,
                dest_filename="copied.md",
            ),
            _op(
                "fs_rename",
                source_folder_id=env.folder_id,
                source_filename="rename.md",
                dest_folder_id=dest_id,
                dest_filename="renamed.md",
            ),
        ]
    )

    assert result["ok"] is True
    assert result["batch_results"][0]["folder_id"] == dest_id
    assert result["batch_results"][1]["folder_id"] == dest_id
    assert (env.repo / dest_id / "copied.md").read_text() == "copy"
    assert (env.repo / dest_id / "renamed.md").read_text() == "rename"


def test_batch_validation_is_all_or_nothing(env):
    result = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="would-create.md",
                content="new",
            ),
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="missing.md",
                content="bad",
            ),
        ]
    )

    assert result["ok"] is False
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert not (env.repo / env.folder_id / "would-create.md").exists()


def test_batch_rejects_empty_unknown_stale_cas_revision_and_replay(env):
    empty = env.tools.fs_batch([])
    unknown = env.tools.fs_batch([{"op": "unknown", "args": {}}])
    stale_cas = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="cas.md",
                content="x",
            )
        ],
        expected_base_commit="a" * 40,
    )
    created = env.tools.fs_create(env.folder_id, "revision.md", "v1")
    stale_revision = env.tools.fs_batch(
        [
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="revision.md",
                content="v2",
                expected_resource_revision="sha256:" + "0" * 64,
            )
        ]
    )
    first = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="idem.md",
                content="x",
            )
        ],
        idempotency_key="batch-idem",
    )
    replay = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="other.md",
                content="x",
            )
        ],
        idempotency_key="batch-idem",
    )

    assert empty["code"] == "INVALID_CONTENT"
    assert unknown["code"] == "INVALID_CONTENT"
    assert stale_cas["code"] == "BASE_COMMIT_CONFLICT"
    assert stale_revision["code"] == "REVISION_CONFLICT"
    assert created["ok"] is True and first["ok"] is True
    assert replay["code"] == "IDEMPOTENCY_CONFLICT"


def test_batch_enforces_critical_file_invariants(env):
    progress = (env.repo / env.folder_id / "progress.md").read_text(encoding="utf-8")

    create_critical = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="_brief.md",
                content="x",
            )
        ]
    )
    rewrite_progress = env.tools.fs_batch(
        [
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="progress.md",
                content=progress.replace("## Blocked\n- None\n\n", ""),
            )
        ]
    )
    delete_progress = env.tools.fs_batch(
        [
            _op(
                "fs_delete",
                folder_id=env.folder_id,
                filename="progress.md",
            )
        ]
    )

    assert create_critical["code"] == "POLICY_VIOLATION"
    assert rewrite_progress["code"] == "POLICY_VIOLATION"
    assert delete_progress["code"] == "POLICY_VIOLATION"


def test_batch_rejects_traversal_large_content_and_critical_transfer(env):
    dest_id = _add_folder(env)
    traversal = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="../escape.md",
                content="x",
            )
        ]
    )
    too_large = env.tools.fs_batch(
        [
            _op(
                "fs_create",
                folder_id=env.folder_id,
                filename="large.md",
                content="x" * 1_000_001,
            )
        ]
    )
    critical_copy = env.tools.fs_batch(
        [
            _op(
                "fs_copy",
                source_folder_id=env.folder_id,
                source_filename="progress.md",
                dest_folder_id=dest_id,
                dest_filename="copy.md",
            )
        ]
    )
    critical_rename = env.tools.fs_batch(
        [
            _op(
                "fs_rename",
                source_folder_id=env.folder_id,
                source_filename="_brief.md",
                dest_folder_id=dest_id,
                dest_filename="brief.md",
            )
        ]
    )

    assert traversal["code"] == "INVALID_PATH"
    assert too_large["code"] == "CONTENT_TOO_LARGE"
    assert critical_copy["code"] == "POLICY_VIOLATION"
    assert critical_rename["code"] == "POLICY_VIOLATION"
    assert not (env.repo.parent / "escape.md").exists()


def test_batch_allows_append_and_structure_preserving_updates(env):
    env.store.save(
        env.folder_id,
        _now,
        golden_order_additions="- original\n",
    )
    progress_path = env.repo / env.folder_id / "progress.md"
    context_path = env.repo / env.folder_id / "context.md"
    claude_path = env.repo / env.folder_id / "CLAUDE.md"
    progress = progress_path.read_text(encoding="utf-8")
    context = context_path.read_text(encoding="utf-8")
    claude = claude_path.read_text(encoding="utf-8")

    result = env.tools.fs_batch(
        [
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="progress.md",
                content=progress + "| 16:02 | batch | append |\n",
            ),
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="golden-order.md",
                content="- original\n- appended\n",
            ),
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="context.md",
                content=context + "\ncontext addition\n",
            ),
            _op(
                "fs_write",
                folder_id=env.folder_id,
                filename="CLAUDE.md",
                content=claude + "\nresume addition\n",
            ),
        ]
    )

    assert result["ok"] is True
    assert len(result["batch_results"]) == 4
    assert "batch | append" in progress_path.read_text(encoding="utf-8")
    assert "appended" in (
        env.repo / env.folder_id / "golden-order.md"
    ).read_text(encoding="utf-8")


# MCP schema and public envelopes -------------------------------------------


def test_mcp_discovery_and_mutation_use_exact_id_filename_schema(env):
    created = _mcp_call(
        "fs_create",
        {
            "folder_id": env.folder_id,
            "filename": "mcp.md",
            "content": "one\ntwo\n",
        },
    )
    read = _mcp_call(
        "fs_read",
        {
            "folder_id": env.folder_id,
            "filename": "mcp.md",
            "offset": 2,
            "limit": 1,
        },
    )
    edited = _mcp_call(
        "fs_edit",
        {
            "folder_id": env.folder_id,
            "filename": "mcp.md",
            "old_string": "two",
            "new_string": "three",
        },
    )

    assert created["filename"] == "mcp.md"
    assert read["content"] == "2\ttwo"
    assert edited["content"] == "one\nthree\n"
    for result in (created, read, edited):
        _assert_safe(result, env.repo)


def test_mcp_cross_folder_and_batch_fields(env):
    dest_id = _add_folder(env)
    env.tools.fs_create(env.folder_id, "source.md", "payload")

    copied = _mcp_call(
        "fs_copy",
        {
            "source_folder_id": env.folder_id,
            "source_filename": "source.md",
            "dest_folder_id": dest_id,
            "dest_filename": "copied.md",
        },
    )
    batched = _mcp_call(
        "fs_batch",
        {
            "operations": [
                _op(
                    "fs_create",
                    folder_id=dest_id,
                    filename="batch.md",
                    content="batch",
                )
            ]
        },
    )

    assert copied["folder_id"] == dest_id
    assert copied["filename"] == "copied.md"
    assert batched["batch_results"] == [
        {
            "op": "fs_create",
            "folder_id": dest_id,
            "filename": "batch.md",
        }
    ]


def test_all_id_only_fs_tools_are_triggerable_through_mcp(env):
    dest_id = _add_folder(env)
    results = [
        _mcp_call("fs_capabilities"),
        _mcp_call("fs_resolve", {"folder_id": env.folder_id}),
        _mcp_call(
            "fs_stat",
            {"folder_id": env.folder_id, "filename": "progress.md"},
        ),
        _mcp_call("fs_list", {"folder_id": env.folder_id}),
    ]
    created = _mcp_call(
        "fs_create",
        {
            "folder_id": env.folder_id,
            "filename": "all-tools.md",
            "content": "v1",
        },
    )
    results.append(created)
    results.append(
        _mcp_call(
            "fs_read",
            {"folder_id": env.folder_id, "filename": "all-tools.md"},
        )
    )
    results.append(
        _mcp_call(
            "fs_write",
            {
                "folder_id": env.folder_id,
                "filename": "all-tools.md",
                "content": "v2",
                "expected_resource_revision": created["content_revision"],
            },
        )
    )
    results.append(
        _mcp_call(
            "fs_edit",
            {
                "folder_id": env.folder_id,
                "filename": "all-tools.md",
                "old_string": "v2",
                "new_string": "v3",
            },
        )
    )
    results.append(
        _mcp_call(
            "fs_copy",
            {
                "source_folder_id": env.folder_id,
                "source_filename": "all-tools.md",
                "dest_folder_id": dest_id,
                "dest_filename": "copied.md",
            },
        )
    )
    results.append(
        _mcp_call(
            "fs_rename",
            {
                "source_folder_id": dest_id,
                "source_filename": "copied.md",
                "dest_folder_id": dest_id,
                "dest_filename": "renamed.md",
            },
        )
    )
    results.append(
        _mcp_call(
            "fs_delete",
            {"folder_id": env.folder_id, "filename": "all-tools.md"},
        )
    )
    results.append(
        _mcp_call(
            "fs_batch",
            {
                "operations": [
                    _op(
                        "fs_create",
                        folder_id=dest_id,
                        filename="batch-trigger.md",
                        content="batch",
                    )
                ]
            },
        )
    )

    assert all(result["ok"] is True for result in results)
    for result in results:
        _assert_safe(result, env.repo)


def test_mcp_errors_preserve_machine_codes_without_locator_leaks(env):
    created = env.tools.fs_create(env.folder_id, "revision-mcp.md", "v1")
    results = [
        _mcp_call(
            "fs_read",
            {"folder_id": env.folder_id, "filename": "../escape.md"},
        ),
        _mcp_call(
            "fs_create",
            {
                "folder_id": env.folder_id,
                "filename": "cas-mcp.md",
                "content": "x",
                "expected_base_commit": "a" * 40,
            },
        ),
        _mcp_call(
            "fs_write",
            {
                "folder_id": env.folder_id,
                "filename": "revision-mcp.md",
                "content": "v2",
                "expected_resource_revision": "sha256:" + "0" * 64,
            },
        ),
        _mcp_call(
            "fs_batch",
            {
                "operations": [
                    _op(
                        "fs_create",
                        folder_id=env.folder_id,
                        filename=".katana/escape.md",
                        content="x",
                    )
                ]
            },
        ),
    ]

    assert [result["code"] for result in results] == [
        "INVALID_PATH",
        "BASE_COMMIT_CONFLICT",
        "REVISION_CONFLICT",
        "INVALID_PATH",
    ]
    assert created["ok"] is True
    for result in results:
        _assert_safe(result, env.repo)


def test_mcp_schema_has_no_legacy_path_or_glob(env):
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}

    assert "fs_glob" not in tools
    assert "path" not in tools["fs_create"].parameters["properties"]
    assert "path_or_id" not in tools["fs_resolve"].parameters["properties"]
    assert set(tools["fs_copy"].parameters["required"]) >= {
        "source_folder_id",
        "source_filename",
        "dest_folder_id",
        "dest_filename",
    }
    assert list(inspect.signature(FSTools.fs_resolve).parameters) == [
        "self",
        "folder_id",
        "filename",
    ]


def test_success_and_error_envelopes_never_leak_locator(env):
    success = env.tools.fs_resolve(env.folder_id)
    error = env.tools.fs_read(env.folder_id, "../escape.md")

    _assert_safe(success, env.repo)
    _assert_safe(error, env.repo)
    assert success["ok"] is True
    assert error["ok"] is False
    assert set(error) >= {"ok", "code", "message", "retryable"}


def test_each_successful_mutation_advances_head_and_keeps_tree_clean(env):
    before = head_sha(str(env.repo))
    result = env.tools.fs_create(env.folder_id, "commit.md", "content")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=env.repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert result["commit"] != before
    assert result["commit"] == head_sha(str(env.repo))
    assert status == ""
