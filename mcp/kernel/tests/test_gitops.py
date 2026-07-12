"""Unit tests for gitops primitives."""

import os
import subprocess
import tempfile

import pytest

from katana_kernel.gitops import (
    CASRejectionError,
    _restore_tree,
    cas_guard,
    git_commit,
    head_sha,
    is_working_tree_clean,
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
    result = git_commit(git_repo, "empty", [])
    assert result["committed"] is False


def test_cas_guard_rejects_stale_sha(git_repo):
    with pytest.raises(CASRejectionError):
        cas_guard(git_repo, "a" * 40)


def test_cas_guard_passes_current_sha(git_repo):
    sha = head_sha(git_repo)
    cas_guard(git_repo, sha)


def test_cas_guard_skips_on_none(git_repo):
    cas_guard(git_repo, None)


def test_restore_tree_cleans_dirty(git_repo):
    with open(os.path.join(git_repo, "dirty.txt"), "w") as f:
        f.write("dirty")
    assert not is_working_tree_clean(git_repo)
    _restore_tree(git_repo)
    assert is_working_tree_clean(git_repo)
    assert not os.path.exists(os.path.join(git_repo, "dirty.txt"))


def test_restore_tree_cleans_untracked(git_repo):
    os.makedirs(os.path.join(git_repo, "untracked_dir"), exist_ok=True)
    with open(os.path.join(git_repo, "untracked.txt"), "w") as f:
        f.write("u")
    _restore_tree(git_repo)
    assert not os.path.exists(os.path.join(git_repo, "untracked.txt"))
    assert not os.path.exists(os.path.join(git_repo, "untracked_dir"))