"""Regression tests for EK-1 governed commit atomicity.

Covers the two root causes from the merge spec: a lost CAS ref-publish race
must leave no dirty scene and must surface a retryable ``BASE_COMMIT_CONFLICT``
rather than a BROKEN fail-stop, and the post-commit index synchronization must
never contend on ``.git/index.lock``.
"""

import os
import shutil
import subprocess
import tempfile
import threading

import pytest

from katana_kernel import (
    BaseCommitConflictError,
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    TransactionManifest,
    git_commit,
    head_sha,
)
from katana_kernel.gitops import FileImage, _commit_exact
from katana_kernel.policy import DomainPolicy


def _porcelain(repo):
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


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
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def workfolder_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    with open(os.path.join(d, ".gitkeep"), "w") as f:
        f.write("")
    with open(os.path.join(d, ".gitignore"), "w") as f:
        f.write("/.katana/runtime/\n")
    with open(os.path.join(d, "note.md"), "w") as f:
        f.write("note-v0\n")
    subprocess.run(
        ["git", "add", ".gitkeep", ".gitignore", "note.md"],
        cwd=d, check=True, capture_output=True,
    )
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, check=True, capture_output=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _policy():
    return DomainPolicy("memory", {"create", "edit"})


def _bind_workfolder_like(repo, *, mutation_ledger=False):
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo)
    ledger = ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json"))
    runtime = os.path.join(repo, ".katana", "runtime")
    manifest = TransactionManifest(
        os.path.join(runtime, "manifests"), git_tracked=False,
    )
    kwargs = {}
    if mutation_ledger:
        from katana_kernel import SQLiteMutationLedger
        kwargs["mutation_ledger"] = SQLiteMutationLedger(
            os.path.join(runtime, "mutations.sqlite"),
        )
    kernel.bind("memory", _policy(), vfs, ledger, manifest, repo, **kwargs)
    return kernel


def test_commit_exact_reports_retryable_conflict_on_publish_failure(git_repo):
    repo = git_repo
    base = head_sha(repo)
    path = os.path.join(repo, "file.txt")
    with open(path, "w") as f:
        f.write("hello")
    # Advance HEAD so the CAS publish against the stale base is lost.
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "concurrent winner"],
        cwd=repo, check=True, capture_output=True,
    )
    winner = head_sha(repo)

    result = _commit_exact(
        repo,
        "test commit",
        {"file.txt": FileImage(True, b"hello", 0o644)},
        base_sha=base,
        amend=False,
    )

    assert result["committed"] is False
    assert result["code"] == "BASE_COMMIT_CONFLICT"
    assert result["retryable"] is True
    assert result["head"] == winner


def test_concurrent_same_base_publish_has_one_winner_one_retryable_loser(git_repo):
    repo = git_repo
    base = head_sha(repo)

    for name in ("a.md", "b.md"):
        with open(os.path.join(repo, name), "w") as f:
            f.write(name)

    results = []

    def _writer(name):
        results.append(_commit_exact(
            repo,
            f"commit {name}",
            {name: FileImage(True, name.encode(), 0o644)},
            base_sha=base,
            amend=False,
        ))

    threads = [
        threading.Thread(target=_writer, args=(name,)) for name in ("a.md", "b.md")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    committed = [r for r in results if r["committed"]]
    conflicts = [r for r in results if r.get("code") == "BASE_COMMIT_CONFLICT"]

    assert len(committed) == 1
    assert len(conflicts) == 1
    assert conflicts[0]["retryable"] is True
    assert conflicts[0]["head"] == committed[0]["detail"]
    assert head_sha(repo) == committed[0]["detail"]


def test_index_sync_does_not_contend_on_index_lock(git_repo):
    repo = git_repo
    git_dir = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    lock_path = os.path.join(repo, git_dir, "index.lock")
    with open(lock_path, "w") as f:
        f.write("999999\n")

    path = os.path.join(repo, "file.txt")
    with open(path, "w") as f:
        f.write("hello")

    result = git_commit(repo, "isolated sync", [path])

    assert result["committed"] is True
    assert _porcelain(repo) == ""


def test_mutate_lost_cas_race_rolls_back_and_raises_retryable(
    workfolder_repo, monkeypatch,
):
    repo = workfolder_repo
    kernel = _bind_workfolder_like(repo)
    base = head_sha(repo)
    winner = "f" * 40

    def _write(binding, args):
        binding.vfs.write("card.md", "content", op="create", args=args)
        return {"id": "m-test", "changed_paths": ["card.md"]}

    monkeypatch.setattr(
        "katana_kernel.kernel.git_commit",
        lambda *a, **kw: {
            "committed": False,
            "code": "BASE_COMMIT_CONFLICT",
            "retryable": True,
            "detail": "cannot lock ref",
            "head": winner,
        },
    )

    with pytest.raises(BaseCommitConflictError) as excinfo:
        kernel.mutate(
            "memory", "create",
            {"body": "x"},
            expected_base_sha=base,
            write_fn=_write,
            commit_msg="test",
        )

    assert excinfo.value.retryable is True
    assert excinfo.value.head == winner
    assert not os.path.exists(os.path.join(repo, "card.md"))
    assert _porcelain(repo) == ""