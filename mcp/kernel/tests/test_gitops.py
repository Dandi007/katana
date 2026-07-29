"""Unit tests for gitops primitives."""

import os
import subprocess
import tempfile

import pytest

from katana_kernel.gitops import (
    CASRejectionError,
    DirtyWorkTreeError,
    MutationLockError,
    RollbackSafetyError,
    _restore_tree,
    cas_guard,
    changed_transaction_paths,
    git_commit,
    head_sha,
    is_working_tree_clean,
    repository_mutation_lock,
    require_clean_working_tree,
    rollback_transaction_paths,
    validate_transaction_paths,
)


@pytest.fixture
def git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    with open(os.path.join(d, ".gitkeep"), "w") as f:
        f.write("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, check=True, capture_output=True)
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_head_sha_returns_sha(git_repo):
    sha = head_sha(git_repo)
    assert len(sha) == 40


def test_is_working_tree_clean(git_repo):
    assert is_working_tree_clean(git_repo)


def test_is_working_tree_dirty(git_repo):
    with open(os.path.join(git_repo, "dirty.txt"), "w") as f:
        f.write("dirty")
    assert not is_working_tree_clean(git_repo)


def test_git_commit_creates_commit(git_repo):
    path = os.path.join(git_repo, "file.txt")
    with open(path, "w") as f:
        f.write("hello")
    result = git_commit(git_repo, "test commit", [path])
    assert result["committed"] is True


def test_git_commit_noop(git_repo):
    with pytest.raises(RollbackSafetyError, match="must not be empty"):
        git_commit(git_repo, "empty", [])


def test_cas_guard_rejects_stale_sha(git_repo):
    with pytest.raises(CASRejectionError):
        cas_guard(git_repo, "a" * 40)


def test_cas_guard_passes_current_sha(git_repo):
    sha = head_sha(git_repo)
    cas_guard(git_repo, sha)


def test_cas_guard_skips_on_none(git_repo):
    cas_guard(git_repo, None)


def test_require_clean_working_tree_rejects_untracked(git_repo):
    with open(os.path.join(git_repo, "sentinel.txt"), "w") as f:
        f.write("do not delete")
    with pytest.raises(DirtyWorkTreeError, match="untracked"):
        require_clean_working_tree(git_repo)
    assert open(os.path.join(git_repo, "sentinel.txt")).read() == "do not delete"


def test_require_clean_working_tree_rejects_tracked_dirty(git_repo):
    keep = os.path.join(git_repo, ".gitkeep")
    with open(keep, "w") as f:
        f.write("dirty preimage")
    with pytest.raises(DirtyWorkTreeError, match="tracked"):
        require_clean_working_tree(git_repo)
    assert open(keep).read() == "dirty preimage"


def test_fail_stop_preserves_tracked_change_and_unlisted_sentinel(git_repo):
    base_sha = require_clean_working_tree(git_repo)
    keep = os.path.join(git_repo, ".gitkeep")
    with open(keep, "w") as f:
        f.write("transaction")
    with open(os.path.join(git_repo, "sentinel.txt"), "w") as f:
        f.write("unrelated")

    result = rollback_transaction_paths(git_repo, base_sha, [".gitkeep"])

    assert result["state"] == "BROKEN"
    assert open(keep).read() == "transaction"
    assert open(os.path.join(git_repo, "sentinel.txt")).read() == "unrelated"


def test_fail_stop_preserves_new_transaction_path_and_unlisted_sentinel(git_repo):
    base_sha = require_clean_working_tree(git_repo)
    with open(os.path.join(git_repo, "created.txt"), "w") as f:
        f.write("transaction")
    with open(os.path.join(git_repo, "sentinel.txt"), "w") as f:
        f.write("unrelated")

    result = rollback_transaction_paths(git_repo, base_sha, ["created.txt"])

    assert result["state"] == "BROKEN"
    assert os.path.exists(os.path.join(git_repo, "created.txt"))
    assert open(os.path.join(git_repo, "sentinel.txt")).read() == "unrelated"


def test_rollback_preserves_scene_when_head_changed(git_repo):
    base_sha = require_clean_working_tree(git_repo)
    path = os.path.join(git_repo, "committed.txt")
    with open(path, "w") as f:
        f.write("committed transaction")
    subprocess.run(["git", "add", "committed.txt"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "transaction"], cwd=git_repo, check=True)

    result = rollback_transaction_paths(git_repo, base_sha, ["committed.txt"])

    assert result["state"] == "BROKEN"
    assert os.path.exists(path)
    assert open(path).read() == "committed transaction"


def test_changed_transaction_paths_includes_tracked_staged_and_untracked(git_repo):
    with open(os.path.join(git_repo, ".gitkeep"), "w") as f:
        f.write("tracked")
    with open(os.path.join(git_repo, "staged.txt"), "w") as f:
        f.write("staged")
    subprocess.run(["git", "add", "staged.txt"], cwd=git_repo, check=True)
    with open(os.path.join(git_repo, "untracked.txt"), "w") as f:
        f.write("untracked")

    assert set(changed_transaction_paths(git_repo)) == {
        ".gitkeep", "staged.txt", "untracked.txt",
    }


@pytest.mark.parametrize(
    "paths",
    [[], [""], ["../escape"], ["/tmp/escape"], [".git/config"]],
)
def test_validate_transaction_paths_rejects_empty_or_escape(git_repo, paths):
    with pytest.raises(RollbackSafetyError):
        validate_transaction_paths(git_repo, paths)


def test_validate_transaction_paths_rejects_symlink(git_repo):
    os.symlink("/tmp", os.path.join(git_repo, "link"))
    with pytest.raises(RollbackSafetyError, match="symlink"):
        validate_transaction_paths(git_repo, ["link/escape.txt"])


def test_legacy_restore_tree_fails_closed_without_modifying_repo(git_repo):
    sentinel = os.path.join(git_repo, "sentinel.txt")
    with open(sentinel, "w") as f:
        f.write("keep")
    with pytest.raises(RollbackSafetyError, match="repository-wide restore is disabled"):
        _restore_tree(git_repo)
    assert open(sentinel).read() == "keep"


def test_rollback_rejects_directory_allowlist_without_modifying_repo(git_repo):
    os.makedirs(os.path.join(git_repo, "tracked-dir"))
    child = os.path.join(git_repo, "tracked-dir", "child.txt")
    with open(child, "w") as f:
        f.write("base")
    subprocess.run(["git", "add", "tracked-dir/child.txt"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-m", "add tree"], cwd=git_repo, check=True)
    base_sha = head_sha(git_repo)
    with open(child, "w") as f:
        f.write("transaction")

    with pytest.raises(RollbackSafetyError, match="directory"):
        rollback_transaction_paths(git_repo, base_sha, ["tracked-dir"])
    assert open(child).read() == "transaction"


def test_linked_worktrees_share_common_directory_mutation_lock(git_repo, tmp_path):
    linked = tmp_path / "linked"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "linked-test", str(linked)],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )

    with repository_mutation_lock(git_repo):
        with pytest.raises(MutationLockError, match="timed out"):
            with repository_mutation_lock(
                str(linked), timeout_seconds=0, poll_seconds=0.01,
            ):
                pass
