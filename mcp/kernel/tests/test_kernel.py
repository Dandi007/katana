"""Unit tests for GovernedKernel."""

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from katana_kernel.gitops import (
    CASRejectionError,
    DirtyWorkTreeError,
    RollbackSafetyError,
    git_commit as exact_git_commit,
    head_sha,
)
from katana_kernel.kernel import GovernedKernel, MutationBrokenError
from katana_kernel.ledger import ResourceIdLedger
from katana_kernel.manifest import TransactionManifest
from katana_kernel.policy import DomainPolicy
from katana_kernel.vfs import GovernedVFS


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
def memory_domain_policy():
    def _memory_invariants(domain, op, args):
        if op == "create" and "body" in args:
            body = args["body"]
            if "## Fact" not in body:
                raise ValueError("body must contain '## Fact' section")
            if "## How to Verify" not in body:
                raise ValueError("body must contain '## How to Verify' section")
        if op in ("update", "edit") and "body" in args:
            body = args["body"]
            if body is not None:
                if "## Fact" not in body:
                    raise ValueError("body must contain '## Fact' section")
                if "## How to Verify" not in body:
                    raise ValueError("body must contain '## How to Verify' section")

    return DomainPolicy(
        domain="memory",
        allowed_ops={"create", "update", "delete", "edit", "list", "get", "read"},
        invariants=[_memory_invariants],
    )


def test_kernel_bind_and_get(git_repo, memory_domain_policy):
    k = GovernedKernel()
    vfs = GovernedVFS(git_repo)
    ledger = ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(git_repo, ".katana", "manifests"))
    binding = k.bind("memory", memory_domain_policy, vfs, ledger, manifest, git_repo)
    assert k.get_binding("memory") is binding


def test_kernel_duplicate_binding_rejected(git_repo, memory_domain_policy):
    k = GovernedKernel()
    vfs = GovernedVFS(git_repo)
    ledger = ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(git_repo, ".katana", "manifests"))
    k.bind("memory", memory_domain_policy, vfs, ledger, manifest, git_repo)
    with pytest.raises(ValueError, match="already bound"):
        k.bind("memory", memory_domain_policy, vfs, ledger, manifest, git_repo)


def test_kernel_duplicate_repo_root_rejected(git_repo, memory_domain_policy):
    k = GovernedKernel()
    vfs = GovernedVFS(git_repo)
    ledger = ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(git_repo, ".katana", "manifests"))
    k.bind("memory", memory_domain_policy, vfs, ledger, manifest, git_repo)
    with pytest.raises(ValueError, match="already bound"):
        k.bind("wiki", memory_domain_policy, vfs, ledger, manifest, git_repo)


def test_kernel_mutate_orchestrates_full_chain(git_repo, memory_domain_policy):
    k = GovernedKernel()
    vfs = GovernedVFS(git_repo)
    ledger = ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(git_repo, ".katana", "manifests"))
    k.bind("memory", memory_domain_policy, vfs, ledger, manifest, git_repo)

    from katana_kernel.gitops import head_sha
    sha = head_sha(git_repo)

    def _write(binding, args):
        binding.vfs.write("card1.md", args["content"], op="create", args=args)
        return {"id": "m-test01", "name": "card1", "changed_paths": ["card1.md"]}

    result = k.mutate(
        "memory", "create",
        {"body": "## Fact\ntest\n\n## How to Verify\nrun", "content": "---\nid: m-test01\n---\n\n## Fact\ntest\n\n## How to Verify\nrun\n"},
        expected_base_sha=sha,
        write_fn=_write,
        commit_msg="test: create card",
    )
    assert result["git"]["committed"] is True
    assert result["manifest"]["manifest_id"] is not None


def test_kernel_mutate_rejects_bad_invariant(git_repo, memory_domain_policy):
    k = GovernedKernel()
    vfs = GovernedVFS(git_repo)
    ledger = ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(git_repo, ".katana", "manifests"))
    k.bind("memory", memory_domain_policy, vfs, ledger, manifest, git_repo)

    def _write(binding, args):
        return {"id": "m-test01", "name": "card1"}

    with pytest.raises(ValueError, match="## Fact"):
        k.mutate("memory", "create", {"body": "no fact here"}, write_fn=_write, commit_msg="test")


def test_kernel_mutate_rejects_stale_cas(git_repo, memory_domain_policy):
    k = GovernedKernel()
    vfs = GovernedVFS(git_repo)
    ledger = ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(git_repo, ".katana", "manifests"))
    k.bind("memory", memory_domain_policy, vfs, ledger, manifest, git_repo)

    def _write(binding, args):
        return {"id": "m-test01", "name": "card1"}

    from katana_kernel.gitops import CASRejectionError
    with pytest.raises(CASRejectionError):
        k.mutate(
            "memory", "create",
            {"body": "## Fact\ntest\n\n## How to Verify\nrun"},
            expected_base_sha="a" * 40,
            write_fn=_write,
            commit_msg="test",
        )


def _bound_kernel(git_repo, memory_domain_policy):
    kernel = GovernedKernel()
    kernel.bind(
        "memory",
        memory_domain_policy,
        GovernedVFS(git_repo),
        ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json")),
        TransactionManifest(os.path.join(git_repo, ".katana", "manifests")),
        git_repo,
    )
    return kernel


def _valid_args():
    return {
        "body": "## Fact\ntest\n\n## How to Verify\nrun",
        "content": "transaction",
    }


def test_kernel_dirty_untracked_preflight_does_not_call_write_fn(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    sentinel = os.path.join(git_repo, "sentinel.txt")
    with open(sentinel, "w") as f:
        f.write("keep")
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    with pytest.raises(DirtyWorkTreeError):
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert called is False
    assert open(sentinel).read() == "keep"


def test_kernel_dirty_tracked_preflight_preserves_hash_and_skips_write_fn(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    tracked = os.path.join(git_repo, ".gitkeep")
    with open(tracked, "w") as f:
        f.write("user dirty state")
    before = subprocess.run(
        ["sha256sum", tracked], capture_output=True, text=True, check=True,
    ).stdout.split()[0]
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-test01", "changed_paths": [".gitkeep"]}

    with pytest.raises(DirtyWorkTreeError):
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    after = subprocess.run(
        ["sha256sum", tracked], capture_output=True, text=True, check=True,
    ).stdout.split()[0]
    assert called is False
    assert after == before


def test_kernel_write_failure_fail_stops_and_preserves_transaction_scene(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    tracked = os.path.join(git_repo, ".gitkeep")
    created = os.path.join(git_repo, "created.txt")

    def _write(binding, args):
        binding.vfs.write(".gitkeep", "changed")
        binding.vfs.write("created.txt", "new")
        raise ValueError("write failed")

    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert exc_info.value.rollback["state"] == "BROKEN"
    assert open(tracked).read() == "changed"
    assert os.path.exists(created)


def test_kernel_commit_failure_raises_broken_and_preserves_scene(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    base_sha = head_sha(git_repo)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    monkeypatch.setattr(
        "katana_kernel.kernel.git_commit",
        lambda *args, **kwargs: {"committed": False, "detail": "injected failure"},
    )
    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate(
            "memory", "create", _valid_args(), write_fn=_write,
        )

    assert exc_info.value.rollback["state"] == "BROKEN"
    assert head_sha(git_repo) == base_sha
    assert os.path.exists(os.path.join(git_repo, "card.md"))


def test_kernel_commit_exception_raises_broken_and_preserves_scene(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    base_sha = head_sha(git_repo)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    def _commit_boom(*args, **kwargs):
        raise OSError("injected commit exception")

    monkeypatch.setattr("katana_kernel.kernel.git_commit", _commit_boom)
    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert exc_info.value.rollback["state"] == "BROKEN"
    assert head_sha(git_repo) == base_sha
    assert os.path.exists(os.path.join(git_repo, "card.md"))


def test_kernel_amend_failure_raises_broken_and_preserves_scene(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    base_sha = head_sha(git_repo)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    monkeypatch.setattr(
        "katana_kernel.kernel.amend_commit",
        lambda *args, **kwargs: {"committed": False, "detail": "injected failure"},
    )
    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate(
            "memory", "create", _valid_args(), write_fn=_write,
        )

    assert exc_info.value.rollback["state"] == "BROKEN"
    assert head_sha(git_repo) != base_sha
    assert os.path.exists(os.path.join(git_repo, "card.md"))
    assert subprocess.run(
        ["git", "-C", git_repo, "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout


def test_kernel_amend_exception_raises_broken_and_preserves_scene(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    base_sha = head_sha(git_repo)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    def _amend_boom(*args, **kwargs):
        raise OSError("injected amend exception")

    monkeypatch.setattr("katana_kernel.kernel.amend_commit", _amend_boom)
    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert exc_info.value.rollback["state"] == "BROKEN"
    assert head_sha(git_repo) != base_sha
    assert os.path.exists(os.path.join(git_repo, "card.md"))


def test_kernel_empty_changed_path_allowlist_fails_closed(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)

    def _write(binding, args):
        return {"id": "m-test01", "changed_paths": []}

    with pytest.raises(RollbackSafetyError, match="must not be empty"):
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)


def test_kernel_repo_lock_serializes_writers_and_rejects_stale_cas(
    git_repo, memory_domain_policy,
):
    first_kernel = _bound_kernel(git_repo, memory_domain_policy)
    second_kernel = _bound_kernel(git_repo, memory_domain_policy)
    base_sha = head_sha(git_repo)
    first_inside_write = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    second_write_called = False
    outcomes = {}

    def _first_write(binding, args):
        binding.vfs.write("first.md", "first")
        first_inside_write.set()
        assert release_first.wait(timeout=5)
        return {"id": "m-first", "changed_paths": ["first.md"]}

    def _second_write(binding, args):
        nonlocal second_write_called
        second_write_called = True
        binding.vfs.write("second.md", "second")
        return {"id": "m-second", "changed_paths": ["second.md"]}

    def _run_first():
        try:
            outcomes["first"] = first_kernel.mutate(
                "memory", "create", _valid_args(),
                expected_base_sha=base_sha, write_fn=_first_write,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            outcomes["first_error"] = exc

    def _run_second():
        second_started.set()
        try:
            outcomes["second"] = second_kernel.mutate(
                "memory", "create", _valid_args(),
                expected_base_sha=base_sha, write_fn=_second_write,
            )
        except Exception as exc:
            outcomes["second_error"] = exc
        finally:
            second_finished.set()

    first_thread = threading.Thread(target=_run_first)
    second_thread = threading.Thread(target=_run_second)
    first_thread.start()
    assert first_inside_write.wait(timeout=5)
    second_thread.start()
    assert second_started.wait(timeout=5)
    assert not second_finished.wait(timeout=0.2)
    assert second_write_called is False

    release_first.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert outcomes["first"]["git"]["committed"] is True
    assert isinstance(outcomes["second_error"], CASRejectionError)
    assert second_write_called is False
    assert os.path.exists(os.path.join(git_repo, "first.md"))
    assert not os.path.exists(os.path.join(git_repo, "second.md"))


def test_kernel_external_head_drift_before_commit_is_broken_not_overwritten(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    base_sha = head_sha(git_repo)

    def _write(binding, args):
        binding.vfs.write("card.md", "transaction")
        with open(os.path.join(git_repo, "external.md"), "w") as f:
            f.write("external writer")
        subprocess.run(["git", "add", "external.md"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "external writer"],
            cwd=git_repo, check=True, capture_output=True,
        )
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate(
            "memory", "create", _valid_args(),
            expected_base_sha=base_sha, write_fn=_write,
        )

    assert exc_info.value.rollback["state"] == "BROKEN"
    assert head_sha(git_repo) != base_sha
    assert os.path.exists(os.path.join(git_repo, "external.md"))
    assert os.path.exists(os.path.join(git_repo, "card.md"))


def test_kernel_fresh_unborn_repository_first_mutation_succeeds(
    tmp_path, memory_domain_policy,
):
    repo = tmp_path / "unborn"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=repo, check=True,
    )
    kernel = _bound_kernel(str(repo), memory_domain_policy)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-first", "changed_paths": ["card.md"]}

    result = kernel.mutate(
        "memory", "create", _valid_args(), write_fn=_write,
    )

    assert result["git"]["committed"] is True
    assert len(head_sha(str(repo))) == 40
    committed = subprocess.run(
        ["git", "show", "HEAD:card.md"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert committed == "transaction"


def test_kernel_write_failure_preserves_concurrent_unrelated_sentinel(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    sentinel = Path(git_repo) / "concurrent-sentinel.txt"

    def _write(binding, args):
        binding.vfs.write("card.md", "transaction")
        sentinel.write_text("external writer", encoding="utf-8")
        raise RuntimeError("injected failure")

    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert exc_info.value.rollback["state"] == "BROKEN"
    assert sentinel.read_text(encoding="utf-8") == "external writer"
    assert (Path(git_repo) / "card.md").read_text(encoding="utf-8") == "transaction"


def test_kernel_exact_commit_excludes_external_staged_entry(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)

    def _write(binding, args):
        binding.vfs.write("card.md", "transaction")
        external = Path(git_repo) / "external-staged.md"
        external.write_text("user staged content", encoding="utf-8")
        subprocess.run(
            ["git", "add", "external-staged.md"], cwd=git_repo, check=True,
        )
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    result = kernel.mutate(
        "memory", "create", _valid_args(), write_fn=_write,
    )

    assert result["git"]["committed"] is True
    show_external = subprocess.run(
        ["git", "show", "HEAD:external-staged.md"],
        cwd=git_repo, capture_output=True, text=True,
    )
    assert show_external.returncode != 0
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert "external-staged.md" in staged


def test_kernel_rejects_ignored_target_before_modifying_it(
    git_repo, memory_domain_policy,
):
    ignore = Path(git_repo) / ".gitignore"
    ignore.write_text("ignored.md\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore target"],
        cwd=git_repo, check=True, capture_output=True,
    )
    target = Path(git_repo) / "ignored.md"
    target.write_text("user content", encoding="utf-8")
    kernel = _bound_kernel(git_repo, memory_domain_policy)

    def _write(binding, args):
        binding.vfs.write("ignored.md", "transaction")
        return {"id": "m-test01", "changed_paths": ["ignored.md"]}

    with pytest.raises(RollbackSafetyError, match="ignored"):
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert target.read_text(encoding="utf-8") == "user content"


def test_kernel_rejects_assume_unchanged_target_before_modifying_it(
    git_repo, memory_domain_policy,
):
    target = Path(git_repo) / "assumed.md"
    target.write_text("user content", encoding="utf-8")
    subprocess.run(["git", "add", "assumed.md"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add assumed target"],
        cwd=git_repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "assumed.md"],
        cwd=git_repo, check=True,
    )
    kernel = _bound_kernel(git_repo, memory_domain_policy)

    def _write(binding, args):
        binding.vfs.write("assumed.md", "transaction")
        return {"id": "m-test01", "changed_paths": ["assumed.md"]}

    with pytest.raises(RollbackSafetyError, match="assume-unchanged"):
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert target.read_text(encoding="utf-8") == "user content"


def test_kernel_rejects_hardlinked_target_before_modifying_alias(
    git_repo, tmp_path, memory_domain_policy,
):
    target = Path(git_repo) / "hardlinked.md"
    target.write_text("shared user content", encoding="utf-8")
    subprocess.run(["git", "add", "hardlinked.md"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add hardlink target"],
        cwd=git_repo, check=True, capture_output=True,
    )
    outside_alias = tmp_path / "outside-alias.md"
    os.link(target, outside_alias)
    kernel = _bound_kernel(git_repo, memory_domain_policy)

    def _write(binding, args):
        binding.vfs.write("hardlinked.md", "transaction")
        return {"id": "m-test01", "changed_paths": ["hardlinked.md"]}

    with pytest.raises(RollbackSafetyError, match="hard-linked"):
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert target.read_text(encoding="utf-8") == "shared user content"
    assert outside_alias.read_text(encoding="utf-8") == "shared user content"


def test_kernel_final_gate_external_overwrite_never_enters_commit(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    real_git_commit = exact_git_commit

    def _write(binding, args):
        binding.vfs.write("card.md", "transaction")
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    def _overwrite_then_commit(repo_root, message, paths, **kwargs):
        (Path(repo_root) / "card.md").write_text(
            "external overwrite", encoding="utf-8",
        )
        return real_git_commit(repo_root, message, paths, **kwargs)

    monkeypatch.setattr(
        "katana_kernel.kernel.git_commit", _overwrite_then_commit,
    )
    with pytest.raises(MutationBrokenError) as exc_info:
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)

    assert exc_info.value.rollback["state"] == "BROKEN"
    committed = subprocess.run(
        ["git", "show", "HEAD:card.md"],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout
    assert committed == "transaction"
    assert (Path(git_repo) / "card.md").read_text(
        encoding="utf-8",
    ) == "external overwrite"
