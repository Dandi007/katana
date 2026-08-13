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
    FileImage,
    RollbackSafetyError,
    RuntimeStateConfigurationError,
    git_commit as exact_git_commit,
    head_sha,
)
from katana_kernel.kernel import GovernedKernel, MutationBrokenError
from katana_kernel.idempotency import (
    IdempotencyConflictError,
    SQLiteMutationLedger,
    canonical_request_hash,
)
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

    with pytest.raises(DirtyWorkTreeError, match="ignored"):
        kernel.mutate("memory", "create", _valid_args(), write_fn=_write)
    assert target.read_text(encoding="utf-8") == "user content"

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


def _runtime_manifest_kernel(
    git_repo,
    memory_domain_policy,
    *,
    commit_ignore=True,
):
    if commit_ignore:
        ignore = Path(git_repo) / ".gitignore"
        ignore.write_text("/.katana/manifests/\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "ignore runtime manifests"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
    kernel = GovernedKernel()
    manifest = TransactionManifest(
        os.path.join(git_repo, ".katana", "manifests"),
        git_tracked=False,
    )
    kernel.bind(
        "memory",
        memory_domain_policy,
        GovernedVFS(git_repo),
        ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json")),
        manifest,
        git_repo,
    )
    return kernel, manifest


def test_kernel_runtime_manifest_is_ignored_and_not_amended(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel, manifest = _runtime_manifest_kernel(
        git_repo, memory_domain_policy,
    )

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    def _unexpected_amend(*args, **kwargs):
        raise AssertionError("runtime manifest must not amend the Git commit")

    monkeypatch.setattr("katana_kernel.kernel.amend_commit", _unexpected_amend)
    result = kernel.mutate(
        "memory", "create", _valid_args(), write_fn=_write,
    )

    manifest_record = manifest.get_manifest(result["manifest"]["manifest_id"])
    tracked = subprocess.run(
        ["git", "-C", git_repo, "ls-tree", "-r", "HEAD", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    status = subprocess.run(
        ["git", "-C", git_repo, "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert result["git"]["committed"] is True
    assert manifest_record["git"] == result["git"]
    assert not any(path.startswith(".katana/manifests/") for path in tracked)
    assert status == ""


def test_kernel_runtime_manifest_requires_ignored_directory(
    git_repo, memory_domain_policy,
):
    kernel, _ = _runtime_manifest_kernel(
        git_repo, memory_domain_policy, commit_ignore=False,
    )
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    with pytest.raises(RuntimeStateConfigurationError, match="must be ignored"):
        kernel.mutate(
            "memory", "create", _valid_args(), write_fn=_write,
        )

    assert called is False


def test_kernel_runtime_manifest_rejects_tracked_files_in_ignored_directory(
    git_repo, memory_domain_policy,
):
    manifest_dir = Path(git_repo) / ".katana" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "old.json").write_text("{}", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".katana/manifests/old.json"],
        cwd=git_repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed tracked manifest"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    kernel, _ = _runtime_manifest_kernel(
        git_repo, memory_domain_policy,
    )

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    with pytest.raises(
        RuntimeStateConfigurationError,
        match="contains tracked files",
    ):
        kernel.mutate(
            "memory", "create", _valid_args(), write_fn=_write,
        )


def test_kernel_runtime_manifest_keeps_tombstone_tracked(
    git_repo, memory_domain_policy,
):
    target = Path(git_repo) / "card.md"
    target.write_text("delete me", encoding="utf-8")
    subprocess.run(["git", "add", "card.md"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed card"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    kernel, manifest = _runtime_manifest_kernel(
        git_repo, memory_domain_policy,
    )

    def _delete(binding, args):
        binding.vfs.delete("card.md")
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    result = kernel.mutate(
        "memory", "delete", {}, write_fn=_delete,
    )
    tracked = subprocess.run(
        ["git", "-C", git_repo, "ls-tree", "-r", "HEAD", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    assert result["git"]["committed"] is True
    assert ".katana/tombstones.json" in tracked
    assert not any(path.startswith(".katana/manifests/") for path in tracked)
    assert manifest.get_manifest(result["manifest"]["manifest_id"])["git"][
        "committed"
    ] is True


def _idempotent_runtime_kernel(
    git_repo,
    memory_domain_policy,
    *,
    commit_ignore=True,
):
    if commit_ignore:
        ignore = Path(git_repo) / ".gitignore"
        ignore.write_text(
            "/.katana/manifests/\n/.katana/ledger.sqlite*\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", ".gitignore"], cwd=git_repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "ignore runtime mutation state"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )
    manifest = TransactionManifest(
        os.path.join(git_repo, ".katana", "manifests"),
        git_tracked=False,
    )
    mutation_ledger = SQLiteMutationLedger(
        os.path.join(git_repo, ".katana", "ledger.sqlite"),
    )
    kernel = GovernedKernel()
    kernel.bind(
        "memory",
        memory_domain_policy,
        GovernedVFS(git_repo),
        ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json")),
        manifest,
        git_repo,
        mutation_ledger=mutation_ledger,
    )
    return kernel, manifest, mutation_ledger


def test_kernel_idempotent_runtime_mutation_replays_without_cas_or_write(
    git_repo, memory_domain_policy,
):
    kernel, manifest, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    base_sha = head_sha(git_repo)
    write_calls = 0

    def _write(binding, args):
        nonlocal write_calls
        write_calls += 1
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    first = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        expected_base_sha=base_sha,
        write_fn=_write,
        commit_msg="test: idempotent create",
        idempotency_key="session-1:lines-10-20",
        idempotency_payload={"card": "m-test01", "content": "transaction"},
    )
    committed_sha = head_sha(git_repo)
    second = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        expected_base_sha=base_sha,
        write_fn=_write,
        commit_msg="test: idempotent create",
        idempotency_key="session-1:lines-10-20",
        idempotency_payload={"content": "transaction", "card": "m-test01"},
    )

    commit_message = subprocess.run(
        ["git", "-C", git_repo, "log", "-1", "--format=%B"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    manifests = manifest.list_manifests()

    assert first == second
    assert write_calls == 1
    assert head_sha(git_repo) == committed_sha
    assert first["mutation_id"]
    assert len(manifests) == 1
    assert mutation_ledger.get(first["mutation_id"]).state == "COMMITTED"
    assert f"Katana-Mutation-Id: {first['mutation_id']}" in commit_message
    assert "Katana-Idempotency-Key-SHA256: sha256:" in commit_message
    assert "Katana-Request-SHA256: sha256:" in commit_message
    assert "Katana-Postimages-SHA256: sha256:" in commit_message


def test_kernel_committed_replay_uses_verified_head_fast_path(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel, _, _ = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    first = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write,
        idempotency_key="fast-replay",
        idempotency_payload={"content": "transaction"},
    )

    def _unexpected_ancestry_check(*args, **kwargs):
        raise AssertionError("verified HEAD must avoid per-record ancestry scans")

    monkeypatch.setattr(
        "katana_kernel.kernel.commit_is_ancestor",
        _unexpected_ancestry_check,
    )
    replay = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=lambda **_: (_ for _ in ()).throw(
            AssertionError("committed replay must not call write_fn")
        ),
        idempotency_key="fast-replay",
        idempotency_payload={"content": "transaction"},
    )

    assert replay == first


def test_kernel_first_idempotent_response_matches_json_durable_replay(
    git_repo, memory_domain_policy,
):
    kernel, _, _ = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {
            "id": "m-test01",
            "echo": ("first", "second"),
            "changed_paths": ["card.md"],
        }

    first = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write,
        idempotency_key="json-response",
        idempotency_payload={"content": "transaction"},
    )
    replay = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=lambda **_: (_ for _ in ()).throw(
            AssertionError("committed replay must not call write_fn")
        ),
        idempotency_key="json-response",
        idempotency_payload={"content": "transaction"},
    )

    assert first == replay
    assert first["echo"] == ["first", "second"]


def test_kernel_idempotency_payload_conflict_precedes_write(
    git_repo, memory_domain_policy,
):
    kernel, _, _ = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write,
        idempotency_key="same-key",
        idempotency_payload={"content": "first"},
    )
    called = False

    def _unexpected_write(binding, args):
        nonlocal called
        called = True
        raise AssertionError("conflicting request must not call write_fn")

    with pytest.raises(IdempotencyConflictError):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_unexpected_write,
            idempotency_key="same-key",
            idempotency_payload={"content": "different"},
        )

    assert called is False


def test_kernel_clean_prewrite_failure_aborts_and_allows_same_request_retry(
    git_repo, memory_domain_policy,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )

    def _fail_before_write(binding, args):
        raise ValueError("pre-write failure")

    with pytest.raises(ValueError, match="pre-write failure"):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_fail_before_write,
            idempotency_key="retry-safe",
            idempotency_payload={"content": "transaction"},
        )

    [aborted] = mutation_ledger.list_by_states({"ABORTED"})
    assert aborted.attempt == 1

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    result = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write,
        idempotency_key="retry-safe",
        idempotency_payload={"content": "transaction"},
    )

    record = mutation_ledger.get(result["mutation_id"])
    assert record.state == "COMMITTED"
    assert record.attempt == 2


def test_kernel_dirty_failure_marks_ledger_broken_and_blocks_new_mutations(
    git_repo, memory_domain_policy,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )

    def _dirty_failure(binding, args):
        binding.vfs.write("card.md", args["content"])
        raise RuntimeError("write crashed")

    with pytest.raises(MutationBrokenError):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_dirty_failure,
            idempotency_key="broken-key",
            idempotency_payload={"content": "transaction"},
        )

    [broken] = mutation_ledger.list_unresolved()
    assert broken.state == "BROKEN"
    called = False

    def _unexpected_write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-never", "changed_paths": ["never.md"]}

    with pytest.raises(MutationBrokenError, match="unresolved"):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_unexpected_write,
            idempotency_key="new-key",
            idempotency_payload={"content": "new"},
        )

    assert called is False


def test_kernel_reconciles_git_commit_after_sqlite_finalize_failure(
    git_repo, memory_domain_policy, monkeypatch,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    real_finalize = mutation_ledger.finalize
    finalize_calls = 0

    def _fail_finalize_once(*args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise OSError("injected SQLite finalize failure")
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(mutation_ledger, "finalize", _fail_finalize_once)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    with pytest.raises(MutationBrokenError):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_write,
            idempotency_key="recover-key",
            idempotency_payload={"content": "transaction"},
        )

    committed_sha = head_sha(git_repo)
    [prepared] = mutation_ledger.list_unresolved()
    assert prepared.state == "PREPARED"

    restarted, _, restarted_ledger = _idempotent_runtime_kernel(
        git_repo,
        memory_domain_policy,
        commit_ignore=False,
    )
    called = False

    def _unexpected_write(binding, args):
        nonlocal called
        called = True
        raise AssertionError("reconcile must not execute write_fn again")

    result = restarted.mutate(
        "memory",
        "create",
        _valid_args(),
        expected_base_sha=prepared.base_sha,
        write_fn=_unexpected_write,
        idempotency_key="recover-key",
        idempotency_payload={"content": "transaction"},
    )

    assert called is False
    assert result["git"]["detail"] == committed_sha
    assert restarted_ledger.get(prepared.mutation_id).state == "COMMITTED"


def test_kernel_receipt_records_only_effective_git_changes(
    git_repo, memory_domain_policy, monkeypatch,
):
    unchanged = Path(git_repo) / "unchanged.md"
    unchanged.write_text("same", encoding="utf-8")
    subprocess.run(["git", "add", "unchanged.md"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed unchanged file"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    real_finalize = mutation_ledger.finalize
    finalize_calls = 0

    def _fail_finalize_once(*args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise OSError("injected SQLite finalize failure")
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(mutation_ledger, "finalize", _fail_finalize_once)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        binding.vfs.write("unchanged.md", "same")
        return {
            "id": "m-test01",
            "changed_paths": ["card.md", "unchanged.md"],
        }

    with pytest.raises(MutationBrokenError):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_write,
            idempotency_key="effective-paths",
            idempotency_payload={"content": "transaction"},
        )

    [prepared] = mutation_ledger.list_unresolved()
    assert prepared.changed_paths == ["card.md"]
    assert set(prepared.postimages) == {"card.md"}

    restarted, _, restarted_ledger = _idempotent_runtime_kernel(
        git_repo,
        memory_domain_policy,
        commit_ignore=False,
    )
    result = restarted.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=lambda **_: (_ for _ in ()).throw(
            AssertionError("receipt recovery must not call write_fn")
        ),
        idempotency_key="effective-paths",
        idempotency_payload={"content": "transaction"},
    )

    assert result["git"]["detail"] == head_sha(git_repo)
    assert restarted_ledger.get(prepared.mutation_id).state == "COMMITTED"


def test_kernel_receipt_recovery_rejects_git_blob_postimage_mismatch(
    git_repo, memory_domain_policy,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    payload = {"content": "transaction"}
    claim = mutation_ledger.claim(
        domain="memory",
        op="create",
        idempotency_key="forged-postimage",
        request_hash=kernel._request_hash("memory", "create", payload),
        base_sha=head_sha(git_repo),
    )
    prepared_result = {
        "operation_result": {"id": "m-test01"},
        "manifest_id": "manual-manifest",
    }
    expected_postimages = {
        "card.md": kernel._postimage_hash(
            FileImage(True, b"transaction", 0o644)
        ),
    }
    prepared = mutation_ledger.prepare(
        claim.record.mutation_id,
        result=prepared_result,
        changed_paths=["card.md"],
        postimages=expected_postimages,
    )
    commit_message = kernel._receipt_commit_message(
        "test: forged postimage receipt",
        prepared,
        canonical_request_hash(prepared_result),
    )
    (Path(git_repo) / "card.md").write_text("tampered", encoding="utf-8")
    subprocess.run(["git", "add", "card.md"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    called = False

    def _unexpected_write(binding, args):
        nonlocal called
        called = True
        raise AssertionError("invalid receipt must not call write_fn")

    with pytest.raises(MutationBrokenError, match="unresolved"):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_unexpected_write,
            idempotency_key="forged-postimage",
            idempotency_payload=payload,
        )

    assert called is False
    assert mutation_ledger.get(prepared.mutation_id).state == "BROKEN"


def test_kernel_git_reset_marks_committed_idempotency_record_orphaned(
    git_repo, memory_domain_policy,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    base_sha = head_sha(git_repo)

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    result = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write,
        idempotency_key="orphan-key",
        idempotency_payload={"content": "transaction"},
    )
    subprocess.run(
        ["git", "reset", "--hard", base_sha],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )

    with pytest.raises(MutationBrokenError, match="unresolved"):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_write,
            idempotency_key="orphan-key",
            idempotency_payload={"content": "transaction"},
        )

    assert mutation_ledger.get(result["mutation_id"]).state == "ORPHANED"


def test_kernel_missing_runtime_ledger_with_receipts_fails_closed(
    git_repo, memory_domain_policy,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write,
        idempotency_key="lost-ledger",
        idempotency_payload={"content": "transaction"},
    )
    ledger_path = Path(mutation_ledger.path)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{ledger_path}{suffix}").unlink(missing_ok=True)

    restarted, _, _ = _idempotent_runtime_kernel(
        git_repo,
        memory_domain_policy,
        commit_ignore=False,
    )
    called = False

    def _unexpected_write(binding, args):
        nonlocal called
        called = True
        raise AssertionError("missing ledger must block writes")

    with pytest.raises(MutationBrokenError, match="ledger is incomplete"):
        restarted.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_unexpected_write,
            idempotency_key="new-key",
            idempotency_payload={"content": "new"},
        )

    assert called is False


def test_kernel_rejects_caller_supplied_reserved_receipt_trailer(
    git_repo, memory_domain_policy,
):
    kernel, _, _ = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    with pytest.raises(ValueError, match="reserved Katana"):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_write,
            commit_msg="test\n\nKatana-Mutation-Id: spoofed",
            idempotency_key="reserved-trailer",
            idempotency_payload={"content": "transaction"},
        )

    assert called is False


def test_kernel_concurrent_same_key_serializes_to_one_commit_and_one_write(
    git_repo, memory_domain_policy,
):
    first_kernel, _, _ = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    second_kernel, _, _ = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy, commit_ignore=False,
    )
    base_sha = head_sha(git_repo)
    first_inside_write = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    second_write_called = False
    outcomes = {}

    def _first_write(binding, args):
        binding.vfs.write("card.md", args["content"])
        first_inside_write.set()
        assert release_first.wait(timeout=5)
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    def _second_write(binding, args):
        nonlocal second_write_called
        second_write_called = True
        raise AssertionError("second writer must replay")

    def _run_first():
        outcomes["first"] = first_kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            expected_base_sha=base_sha,
            write_fn=_first_write,
            idempotency_key="concurrent-key",
            idempotency_payload={"content": "transaction"},
        )

    def _run_second():
        try:
            outcomes["second"] = second_kernel.mutate(
                "memory",
                "create",
                _valid_args(),
                expected_base_sha=base_sha,
                write_fn=_second_write,
                idempotency_key="concurrent-key",
                idempotency_payload={"content": "transaction"},
            )
        finally:
            second_finished.set()

    first_thread = threading.Thread(target=_run_first)
    second_thread = threading.Thread(target=_run_second)
    first_thread.start()
    assert first_inside_write.wait(timeout=5)
    second_thread.start()
    assert not second_finished.wait(timeout=0.2)

    release_first.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert outcomes["first"] == outcomes["second"]
    assert second_write_called is False
    commits = subprocess.run(
        ["git", "-C", git_repo, "rev-list", "--count", f"{base_sha}..HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert commits == "1"


def test_kernel_reconciles_clean_pending_claim_as_safe_retry(
    git_repo, memory_domain_policy,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    payload = {"content": "transaction"}
    pending = mutation_ledger.claim(
        domain="memory",
        op="create",
        idempotency_key="crashed-before-write",
        request_hash=kernel._request_hash("memory", "create", payload),
        base_sha=head_sha(git_repo),
    )

    def _write(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    result = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write,
        idempotency_key="crashed-before-write",
        idempotency_payload=payload,
    )

    record = mutation_ledger.get(pending.record.mutation_id)
    assert result["mutation_id"] == pending.record.mutation_id
    assert record.state == "COMMITTED"
    assert record.attempt == 2


def test_kernel_pending_claim_with_dirty_scene_fails_closed(
    git_repo, memory_domain_policy,
):
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )
    payload = {"content": "transaction"}
    pending = mutation_ledger.claim(
        domain="memory",
        op="create",
        idempotency_key="crashed-dirty",
        request_hash=kernel._request_hash("memory", "create", payload),
        base_sha=head_sha(git_repo),
    )
    (Path(git_repo) / "partial.md").write_text(
        "uncertain mutation scene",
        encoding="utf-8",
    )
    called = False

    def _unexpected_write(binding, args):
        nonlocal called
        called = True
        raise AssertionError("dirty PENDING scene must fail closed")

    with pytest.raises(MutationBrokenError, match="unresolved"):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=_unexpected_write,
            idempotency_key="crashed-dirty",
            idempotency_payload=payload,
        )

    assert called is False
    assert mutation_ledger.get(pending.record.mutation_id).state == "BROKEN"


def test_kernel_runtime_ledger_requires_all_sqlite_sidecars_ignored(
    git_repo, memory_domain_policy,
):
    ignore = Path(git_repo) / ".gitignore"
    ignore.write_text(
        "/.katana/manifests/\n/.katana/ledger.sqlite\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "incomplete runtime ignore"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    manifest = TransactionManifest(
        os.path.join(git_repo, ".katana", "manifests"),
        git_tracked=False,
    )
    mutation_ledger = SQLiteMutationLedger(
        os.path.join(git_repo, ".katana", "ledger.sqlite"),
    )
    kernel = GovernedKernel()
    kernel.bind(
        "memory",
        memory_domain_policy,
        GovernedVFS(git_repo),
        ResourceIdLedger(os.path.join(git_repo, ".katana", "tombstones.json")),
        manifest,
        git_repo,
        mutation_ledger=mutation_ledger,
    )

    with pytest.raises(
        RuntimeStateConfigurationError,
        match="ledger.sqlite-wal",
    ):
        kernel.mutate(
            "memory",
            "create",
            _valid_args(),
            write_fn=lambda **_: {
                "id": "m-never",
                "changed_paths": ["never.md"],
            },
            idempotency_key="bad-ignore",
        )


def test_kernel_idempotent_noop_is_safely_aborted_not_left_broken(
    git_repo, memory_domain_policy,
):
    target = Path(git_repo) / "card.md"
    target.write_text("transaction", encoding="utf-8")
    subprocess.run(["git", "add", "card.md"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed card"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    kernel, _, mutation_ledger = _idempotent_runtime_kernel(
        git_repo, memory_domain_policy,
    )

    def _write_same_content(binding, args):
        binding.vfs.write("card.md", args["content"])
        return {"id": "m-test01", "changed_paths": ["card.md"]}

    result = kernel.mutate(
        "memory",
        "create",
        _valid_args(),
        write_fn=_write_same_content,
        idempotency_key="noop-key",
        idempotency_payload={"content": "transaction"},
    )

    [aborted] = mutation_ledger.list_by_states({"ABORTED"})
    assert result["git"]["committed"] is False
    assert result["mutation_id"] == aborted.mutation_id
    assert aborted.state == "ABORTED"
    assert mutation_ledger.list_unresolved() == []


def test_kernel_rejects_sqlite_ledger_with_default_tracked_manifest(
    git_repo, memory_domain_policy,
):
    mutation_ledger = SQLiteMutationLedger(
        os.path.join(git_repo, ".katana", "ledger.sqlite"),
    )
    kernel = GovernedKernel()

    with pytest.raises(ValueError, match="runtime, non-Git manifest"):
        kernel.bind(
            "memory",
            memory_domain_policy,
            GovernedVFS(git_repo),
            ResourceIdLedger(
                os.path.join(git_repo, ".katana", "tombstones.json")
            ),
            TransactionManifest(
                os.path.join(git_repo, ".katana", "manifests")
            ),
            git_repo,
            mutation_ledger=mutation_ledger,
        )


def _scope_write(binding, args):
    binding.vfs.write("folder-a/card.md", args["content"], op="create", args=args)
    return {
        "id": "m-test01",
        "name": "card",
        "changed_paths": ["folder-a/card.md"],
    }


def test_kernel_scope_prefixes_ignores_out_of_scope_dirt(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    b_dir = os.path.join(git_repo, "folder-b")
    os.makedirs(b_dir)
    b_tracked = os.path.join(b_dir, "tracked.md")
    with open(b_tracked, "w") as f:
        f.write("b v1\n")
    subprocess.run(
        ["git", "add", "folder-b/tracked.md"], cwd=git_repo, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed folder-b"],
        cwd=git_repo, check=True, capture_output=True,
    )
    with open(b_tracked, "a") as f:
        f.write("b dirty edit\n")
    b_untracked = os.path.join(b_dir, "untracked.md")
    with open(b_untracked, "w") as f:
        f.write("b untracked\n")
    b_tracked_before = open(b_tracked, "rb").read()
    b_untracked_before = open(b_untracked, "rb").read()

    sha = head_sha(git_repo)
    result = kernel.mutate(
        "memory", "create", _valid_args(),
        expected_base_sha=sha,
        write_fn=_scope_write,
        commit_msg="test: scoped create",
        scope_prefixes=["folder-a"],
        control_paths=["INDEX.md"],
    )

    assert result["git"]["committed"] is True
    committed = subprocess.run(
        [
            "git", "diff-tree", "--no-commit-id", "--name-only", "-r",
            result["git"]["detail"],
        ],
        cwd=git_repo, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert "folder-a/card.md" in committed
    assert not any(path.startswith("folder-b/") for path in committed)
    assert open(b_tracked, "rb").read() == b_tracked_before
    assert open(b_untracked, "rb").read() == b_untracked_before


def test_kernel_scope_prefixes_rejects_in_scope_dirt(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    a_dir = os.path.join(git_repo, "folder-a")
    os.makedirs(a_dir)
    with open(os.path.join(a_dir, "dirty.md"), "w") as f:
        f.write("dirty in scope\n")
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-test01", "changed_paths": ["folder-a/card.md"]}

    with pytest.raises(DirtyWorkTreeError):
        kernel.mutate(
            "memory", "create", _valid_args(),
            write_fn=_write,
            scope_prefixes=["folder-a"],
        )
    assert called is False


def test_kernel_scope_prefixes_none_keeps_whole_repo_semantics(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    b_dir = os.path.join(git_repo, "folder-b")
    os.makedirs(b_dir)
    with open(os.path.join(b_dir, "dirty.md"), "w") as f:
        f.write("dirty\n")
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-test01", "changed_paths": ["folder-a/card.md"]}

    with pytest.raises(DirtyWorkTreeError):
        kernel.mutate(
            "memory", "create", _valid_args(), write_fn=_write,
        )
    assert called is False


def test_kernel_scope_prefixes_control_path_dirty_rejects(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    with open(os.path.join(git_repo, "INDEX.md"), "w") as f:
        f.write("stale index\n")
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-test01", "changed_paths": ["folder-a/card.md"]}

    with pytest.raises(DirtyWorkTreeError):
        kernel.mutate(
            "memory", "create", _valid_args(),
            write_fn=_write,
            scope_prefixes=["folder-a"],
            control_paths=["INDEX.md"],
        )
    assert called is False


def test_kernel_scope_prefixes_ledger_control_path_dirty_rejects(
    git_repo, memory_domain_policy,
):
    kernel = _bound_kernel(git_repo, memory_domain_policy)
    ledger_path = os.path.join(git_repo, ".katana", "tombstones.json")
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with open(ledger_path, "w") as f:
        f.write('{"tombstones": []}\n')
    subprocess.run(
        ["git", "add", ".katana/tombstones.json"], cwd=git_repo, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed ledger"],
        cwd=git_repo, check=True, capture_output=True,
    )
    with open(ledger_path, "w") as f:
        f.write('{"tombstones": ["m-deadbe"]}\n')
    called = False

    def _write(binding, args):
        nonlocal called
        called = True
        return {"id": "m-test01", "changed_paths": ["folder-a/card.md"]}

    with pytest.raises(DirtyWorkTreeError):
        kernel.mutate(
            "memory", "create", _valid_args(),
            write_fn=_write,
            scope_prefixes=["folder-a"],
        )
    assert called is False
