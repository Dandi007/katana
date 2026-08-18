"""Regression tests for the EK-2 governed reconcile recovery checklist."""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    MutationBrokenError,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
    canonical_request_hash,
    git_commit,
    head_sha,
)
from katana_kernel.gitops import FileImage
from katana_kernel.policy import DomainPolicy


def _porcelain(repo):
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    for name, content in (
        (".gitkeep", ""),
        (".gitignore", "/.katana/runtime/\n"),
        ("note.md", "note-v0\n"),
    ):
        with open(os.path.join(d, name), "w") as f:
            f.write(content)
    subprocess.run(
        ["git", "add", ".gitkeep", ".gitignore", "note.md"],
        cwd=d, check=True, capture_output=True,
    )
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, check=True, capture_output=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _bind(repo, *, mutation_ledger=True):
    kernel = GovernedKernel()
    runtime = os.path.join(repo, ".katana", "runtime")
    kernel.bind(
        "memory",
        DomainPolicy("memory", {"create", "edit"}),
        GovernedVFS(repo),
        ResourceIdLedger(os.path.join(repo, ".katana", "tombstones.json")),
        TransactionManifest(os.path.join(runtime, "manifests"), git_tracked=False),
        repo,
        mutation_ledger=(
            SQLiteMutationLedger(os.path.join(runtime, "mutations.sqlite"))
            if mutation_ledger
            else None
        ),
    )
    return kernel


def _types(recovered):
    return [item["type"] for item in recovered]


def _prepare_edit_record(kernel, binding, repo, ledger, content, manifest_id="m1"):
    base = head_sha(repo)
    claim = ledger.claim(
        domain="memory", op="edit", idempotency_key=f"key-{manifest_id}",
        request_hash=canonical_request_hash({"content": content.decode()}),
        base_sha=base,
    )
    mutation_id = claim.record.mutation_id
    with open(os.path.join(repo, "note.md"), "wb") as f:
        f.write(content)
    prepared = ledger.prepare(
        mutation_id,
        result={"operation_result": {"id": "m1"}, "manifest_id": manifest_id},
        changed_paths=["note.md"],
        postimages={
            "note.md": kernel._postimage_hash(
                FileImage(True, content, 0o644),
            )
        },
    )
    os.makedirs(binding.manifest.manifests_dir, exist_ok=True)
    with open(
        os.path.join(binding.manifest.manifests_dir, f"{manifest_id}.json"), "w",
    ) as f:
        json.dump({"manifest_id": manifest_id}, f)
    return base, prepared


def test_recover_type1_quarantines_untracked_under_scope(repo):
    kernel = _bind(repo)
    untracked = os.path.join(repo, "scratch.txt")
    with open(untracked, "w") as f:
        f.write("scratch")
    assert "scratch.txt" in _porcelain(repo)

    result = kernel.reconcile("memory", recover=True)

    assert "untracked_quarantined" in _types(result["recovered"])
    assert _porcelain(repo) == ""
    assert not os.path.exists(untracked)
    # Quarantined under the ignored .katana runtime root with an audit pointer.
    recovered_root = os.path.join(repo, ".katana", "runtime", "recovered")
    assert os.path.exists(os.path.join(recovered_root, "quarantine-manifest.json"))


def test_recover_type2_resumes_prepared_commit(repo):
    kernel = _bind(repo)
    binding = kernel.get_binding("memory")
    ledger = binding.mutation_ledger
    base, _ = _prepare_edit_record(
        kernel, binding, repo, ledger, b"note-v1\n", manifest_id="m2",
    )
    assert head_sha(repo) == base

    result = kernel.reconcile("memory", recover=True)

    assert "resume_commit" in _types(result["recovered"])
    assert "ledger_reconciled" in _types(result["recovered"])
    assert _porcelain(repo) == ""
    assert ledger.list_by_states({"PENDING", "PREPARED"}) == []


def test_recover_type3_unstages_index_only_entry(repo):
    kernel = _bind(repo)
    keep = os.path.join(repo, ".gitkeep")
    with open(keep, "w") as f:
        f.write("staged-change")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, check=True)
    with open(keep, "w") as f:
        f.write("")
    assert ".gitkeep" in _porcelain(repo)

    result = kernel.reconcile("memory", recover=True)

    assert "index_only_staged" in _types(result["recovered"])
    assert _porcelain(repo) == ""


def test_recover_type4_finalizes_prepared_receipt_on_clean_tree(repo):
    kernel = _bind(repo)
    binding = kernel.get_binding("memory")
    ledger = binding.mutation_ledger
    base, prepared = _prepare_edit_record(
        kernel, binding, repo, ledger, b"note-v1\n", manifest_id="m4",
    )
    message = kernel._receipt_commit_message(
        f"chore({prepared.domain}): {prepared.op}",
        prepared,
        canonical_request_hash(prepared.result),
    )
    committed = git_commit(repo, message, ["note.md"], expected_base_sha=base)
    assert committed["committed"] is True
    assert _porcelain(repo) == ""

    result = kernel.reconcile("memory", recover=True)

    assert "ledger_reconciled" in _types(result["recovered"])
    assert ledger.list_by_states({"PENDING", "PREPARED"}) == []


def test_recover_type5_removes_orphan_index_lock(repo):
    kernel = _bind(repo)
    lock_path = os.path.join(repo, ".git", "index.lock")
    with open(lock_path, "w") as f:
        f.write("999999\n")

    result = kernel.reconcile("memory", recover=True)

    assert "orphan_index_lock" in _types(result["recovered"])
    assert not os.path.exists(lock_path)
    assert _porcelain(repo) == ""


def test_recover_type6_unrecoverable_scene_fails_closed_without_touching_tree(repo):
    kernel = _bind(repo)
    note = os.path.join(repo, "note.md")
    with open(note, "w") as f:
        f.write("unattributable drift\n")
    before = open(note).read()

    with pytest.raises(MutationBrokenError) as excinfo:
        kernel.reconcile("memory", recover=True)

    rollback = excinfo.value.rollback
    assert rollback.get("state") == "BROKEN"
    assert "note.md" in rollback.get("paths", [])
    assert rollback.get("suggested_commands")
    assert open(note).read() == before
    assert "note.md" in _porcelain(repo)


def test_reconcile_verify_preserves_head_and_unresolved_keys(repo):
    kernel = _bind(repo)
    result = kernel.reconcile("memory")
    assert result["ok"] is True
    assert result["head"] == head_sha(repo)
    assert result["unresolved"] == 0
    assert "recovered" not in result