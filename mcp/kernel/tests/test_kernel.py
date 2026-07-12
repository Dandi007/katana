"""Unit tests for GovernedKernel."""

import os
import shutil
import subprocess
import tempfile

import pytest

from katana_kernel.kernel import GovernedKernel
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