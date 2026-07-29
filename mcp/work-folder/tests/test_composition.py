"""GovernedKernel + flat WorkFolderStore composition contracts."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from katana_kernel import (
    CASRejectionError,
    DomainPolicy,
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
    head_sha,
    is_working_tree_clean,
)
from katana_kernel.policy import PolicyViolationError
from katana_work_folder_mcp import lifecycle
from katana_work_folder_mcp.brief import parse_brief
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


def _fixed_now():
    return datetime(2026, 7, 29, 16, 0, 0)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Work Folder Test"], cwd=repo, check=True)
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


def _is_clean(repo: Path) -> bool:
    return is_working_tree_clean(
        str(repo),
        allowed_ignored_paths=[str(repo / ".katana/runtime")],
    )


@pytest.fixture
def repo_store(tmp_path):
    _init_repo(tmp_path)
    kernel, store = _bind(tmp_path)
    return tmp_path, kernel, store


def test_create_is_flat_and_brief_identity_matches(repo_store):
    repo, _, store = repo_store

    result = store.create("test topic", _fixed_now)
    folder_id = result["folder_id"]

    assert result["created"] is True
    assert result["id"] == folder_id
    assert result["seeded"] == ["progress.md", "context.md", "_brief.md"]
    assert (repo / folder_id).parent == repo
    assert parse_brief(
        (repo / folder_id / "_brief.md").read_text(encoding="utf-8")
    )["frontmatter"]["id"] == folder_id
    assert "path" not in result and "folder" not in result


def test_create_and_save_reject_stale_cas(repo_store):
    repo, _, store = repo_store
    with pytest.raises(CASRejectionError):
        store.create("topic", _fixed_now, expected_base_sha="a" * 40)
    assert _is_clean(repo)

    created = store.create("topic", _fixed_now)
    with pytest.raises(CASRejectionError):
        store.save(
            created["folder_id"],
            _fixed_now,
            expected_base_sha="b" * 40,
        )
    assert _is_clean(repo)


def test_sequential_cas_uses_returned_git_sha(repo_store):
    repo, _, store = repo_store
    created = store.create("topic", _fixed_now)
    sha1 = created["git"]["detail"]

    saved = store.save(
        created["folder_id"],
        _fixed_now,
        summary="checkpoint",
        expected_base_sha=sha1,
    )

    assert sha1 != saved["git"]["detail"]
    assert saved["git"]["detail"] == head_sha(str(repo))
    assert _is_clean(repo)


def test_runtime_manifest_is_untracked_and_populates_git_field(repo_store):
    repo, _, store = repo_store
    result = store.create("topic", _fixed_now)
    manifest_id = result["manifest"]["manifest_id"]
    tracked_files = subprocess.run(
        ["git", "ls-tree", "-r", "HEAD", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(
        path
        for path in tracked_files
        if path.startswith(".katana/manifests/")
        or path.startswith(".katana/runtime/")
    )
    manifest_path = next(
        path
        for path in (repo / ".katana/runtime/manifests").glob("*.json")
        if manifest_id in path.name
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["git"]["committed"] is True
    assert len(manifest["git"]["detail"]) == 40
    assert _is_clean(repo)


def test_save_resume_reindex_leave_clean_tree_and_use_folder_id(repo_store):
    repo, _, store = repo_store
    folder_id = store.create("topic", _fixed_now)["folder_id"]

    saved = store.save(
        folder_id,
        _fixed_now,
        summary="checkpoint",
        context_snapshot="# Context\nsnapshot\n",
    )
    resumed = store.resume(folder_id, _fixed_now)
    indexed = store.reindex()

    assert saved["folder_id"] == folder_id
    assert resumed["folder_id"] == folder_id
    assert indexed["indexed"] == 1
    assert "index_path" not in indexed
    assert (repo / "INDEX.md").is_file()
    assert _is_clean(repo)


def test_save_is_append_only_for_golden_order_and_changelog(repo_store):
    repo, _, store = repo_store
    folder_id = store.create("topic", _fixed_now)["folder_id"]

    store.save(
        folder_id,
        _fixed_now,
        summary="save one",
        golden_order_additions="- 第一条\n",
    )
    store.save(
        folder_id,
        _fixed_now,
        summary="save two",
        golden_order_additions="- 第二条\n",
    )

    golden = (repo / folder_id / "golden-order.md").read_text(encoding="utf-8")
    progress = (repo / folder_id / "progress.md").read_text(encoding="utf-8")
    assert golden.count("第一条") == 1
    assert golden.count("第二条") == 1
    assert progress.count("save one") == 1
    assert progress.count("save two") == 1


def test_missing_and_legacy_folder_locators_are_rejected(repo_store):
    _, _, store = repo_store
    with pytest.raises(FileNotFoundError, match="wf-deadbe"):
        store.save("wf-deadbe", _fixed_now)
    with pytest.raises(ValueError, match="invalid folder_id"):
        store.save("2026/07/29/topic", _fixed_now)


def test_folder_identity_mismatch_is_rejected(repo_store):
    repo, _, store = repo_store
    folder_id = store.create("topic", _fixed_now)["folder_id"]
    brief = repo / folder_id / "_brief.md"
    brief.write_text(
        brief.read_text(encoding="utf-8").replace(folder_id, "wf-deadbe", 1),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "corrupt identity"], cwd=repo, check=True)

    with pytest.raises(ValueError, match="identity mismatch"):
        store.save(folder_id, _fixed_now)


def test_reindex_dry_run_is_read_only(repo_store):
    repo, _, store = repo_store
    store.create("topic", _fixed_now)
    sha_before = head_sha(str(repo))
    index_before = (repo / "INDEX.md").read_bytes()

    result = store.reindex(dry_run=True)

    assert "# Work Folder INDEX" in result["preview"]
    assert (repo / "INDEX.md").read_bytes() == index_before
    assert head_sha(str(repo)) == sha_before


def test_resume_broken_blocks_through_governed_store(repo_store):
    repo, _, store = repo_store
    folder_id = store.create("broken", _fixed_now)["folder_id"]
    missing = "/nonexistent/composition-broken-xyz"
    context = (
        "# Context\n\n"
        "## 关键路径\n"
        "| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |\n"
        "|------|------------|------------|------|\n"
        f"| target | {missing} | - | probe |\n"
    )
    store.save(folder_id, _fixed_now, context_snapshot=context)

    result = store.resume(folder_id, _fixed_now)

    assert result["verification"]["overall"] == "BROKEN"
    assert result["blocked"] is True
    assert result["contract"] == lifecycle.RESUME_BLOCKED_CONTRACT
    assert missing in result["resume_report"]
    assert _is_clean(repo)


def test_resume_missing_folder_is_fail_safe_without_commit(repo_store):
    repo, _, store = repo_store
    sha_before = head_sha(str(repo))

    result = store.resume("wf-deadbe", _fixed_now)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert head_sha(str(repo)) == sha_before


def test_policy_rejects_empty_topic_and_missing_folder_id(repo_store):
    _, kernel, store = repo_store
    with pytest.raises(PolicyViolationError, match="topic"):
        store.create("", _fixed_now)
    with pytest.raises(PolicyViolationError, match="folder_id"):
        kernel.mutate("work-folder", "wf_save", {})


def test_governed_vfs_rejects_escape_absolute_and_symlink(repo_store):
    repo, kernel, _ = repo_store
    vfs = kernel.get_binding("work-folder").vfs
    with pytest.raises(Exception):
        vfs.write("../escape.md", "x")
    with pytest.raises(Exception):
        vfs.write("/tmp/escape.md", "x")

    vfs.write("legit.md", "content")
    os.symlink(repo / "legit.md", repo / "link.md")
    with pytest.raises(Exception):
        vfs.read_text("link.md")


def test_duplicate_domain_binding_is_rejected(repo_store):
    repo, kernel, _ = repo_store
    binding = kernel.get_binding("work-folder")
    with pytest.raises(ValueError, match="already bound"):
        kernel.bind(
            "work-folder",
            _wf_policy(),
            GovernedVFS(str(repo)),
            binding.ledger,
            binding.manifest,
            str(repo),
        )


def test_different_domain_cannot_bind_same_repo(repo_store):
    repo, kernel, _ = repo_store
    policy = DomainPolicy(domain="memory", allowed_ops={"create"}, invariants=[])
    ledger = ResourceIdLedger(
        str(repo / ".katana" / "memory-tombstones.json"),
        prefix="m-",
    )
    manifest = TransactionManifest(str(repo / ".katana" / "memory-manifests"))

    with pytest.raises(ValueError, match="already bound"):
        kernel.bind(
            "memory",
            policy,
            GovernedVFS(str(repo)),
            ledger,
            manifest,
            str(repo),
        )


def test_tombstoned_id_is_not_reused(repo_store):
    _, kernel, _ = repo_store
    ledger = kernel.get_binding("work-folder").ledger
    tombstoned = ledger.gen_id(set())
    ledger.tombstone(tombstoned)

    assert ledger.is_tombstoned(tombstoned)
    for _ in range(25):
        assert ledger.gen_id({tombstoned}) != tombstoned
