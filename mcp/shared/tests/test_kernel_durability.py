"""Durability / failpoint anchors for the Git-native transaction protocol.

Covers design §6.3/§6.6 no-partial-visibility guarantees the previous attempt
lacked: writer-private staging means pre-publish failures cannot dirty
canonical client-visible state; an initially dirty working tree is fail-stopped
as RECOVERY_REQUIRED; identity-catalog changes are committed atomically with
content; and failed validation after a mint leaves zero catalog delta.
"""
import json
import os

import pytest

from katana_kb_mcp_shared import kernel
from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.catalog import CATALOG_REL, Catalog
from katana_kb_mcp_shared.kernel.errors import KernelError, RECOVERY_REQUIRED
from katana_kb_mcp_shared.kernel.facade import GovernedVFS


class _Policy:
    domain = "test"
    id_prefix = "t-"
    policy_version = 1

    def __init__(self, reject=False):
        self.reject = reject

    def validate(self, batch):
        if self.reject:
            raise KernelError("POLICY_VIOLATION", "rejected by test policy")


def _engine(tmp_path):
    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    eng.repo.ensure_repo()
    return eng


def _vfs(tmp_path, policy=None):
    eng = _engine(tmp_path)
    return GovernedVFS(eng, Catalog(str(tmp_path), id_prefix="t-"),
                       policy or _Policy())


# ── writer-private staging: no dirty working tree on pre-publish failure ──

def test_pre_publish_git_failure_leaves_clean_tree(tmp_path, monkeypatch):
    vfs = _vfs(tmp_path)
    vfs.fs_create(virtual_path="a.md", content="hello\n")
    head = vfs.engine.repo.head()

    # Fail the commit-tree/ref phase; the private index must not touch the
    # canonical working tree, so no partial file appears and HEAD is unchanged.
    orig_publish = vfs.engine.repo.publish

    def boom(*a, **k):
        raise KernelError("COMMIT_FAILED", "simulated object-store failure")

    monkeypatch.setattr(vfs.engine.repo, "publish", boom)
    with pytest.raises(KernelError):
        vfs.fs_create(virtual_path="b.md", content="world\n")
    monkeypatch.setattr(vfs.engine.repo, "publish", orig_publish)

    assert vfs.engine.repo.head() == head
    assert not (tmp_path / "b.md").exists()
    assert not vfs.engine.repo.is_dirty()


def test_dirty_working_tree_is_recovery_required(tmp_path):
    vfs = _vfs(tmp_path)
    vfs.fs_create(virtual_path="a.md", content="hello\n")
    # Corrupt a tracked file out-of-band → unknown dirty pre-state.
    (tmp_path / "a.md").write_text("TAMPERED\n", encoding="utf-8")
    b = MutationBatch(domain="test")
    b.add(Change(op=Op.CREATE, resource_id="t-x", after_path="c.md",
                 after_content=b"x\n"))
    with pytest.raises(KernelError) as ei:
        vfs.engine.commit(b, message="doomed")
    assert ei.value.code == RECOVERY_REQUIRED


# ── catalog atomicity (design §6.1/§6.2, INV-6) ───────────────────────

def test_catalog_committed_atomically_with_content(tmp_path):
    vfs = _vfs(tmp_path)
    r = vfs.fs_create(virtual_path="a.md", content="hello\n")
    # The catalog is in the SAME commit as the content.
    blob = vfs.engine.repo.read_blob_at(vfs.engine.repo.head(), CATALOG_REL)
    assert blob is not None
    catalog = json.loads(blob.decode("utf-8"))
    assert r["resource_id"] in catalog["by_id"]


def test_failed_validation_after_mint_leaves_no_catalog_delta(tmp_path):
    vfs = _vfs(tmp_path, policy=_Policy(reject=True))
    head_none = vfs.engine.repo.head()
    with pytest.raises(KernelError):
        vfs.fs_create(virtual_path="a.md", content="hello\n")
    # No commit, and the in-memory catalog was rolled back (mint discarded).
    assert vfs.engine.repo.head() == head_none
    assert vfs.catalog.id_of("a.md") is None
    assert not (tmp_path / CATALOG_REL).exists() or \
        json.loads((tmp_path / CATALOG_REL).read_text())["by_id"] == {}


def test_rename_catalog_durable_in_commit(tmp_path):
    vfs = _vfs(tmp_path)
    r = vfs.fs_create(virtual_path="a.md", content="x")
    rid = r["resource_id"]
    vfs.fs_rename(resource_id=rid, new_path="sub/b.md")
    blob = vfs.engine.repo.read_blob_at(vfs.engine.repo.head(), CATALOG_REL)
    catalog = json.loads(blob.decode("utf-8"))
    assert catalog["by_id"][rid] == "sub/b.md"


def test_delete_tombstone_durable_in_commit(tmp_path):
    vfs = _vfs(tmp_path)
    r = vfs.fs_create(virtual_path="a.md", content="x")
    rid = r["resource_id"]
    vfs.fs_delete(resource_id=rid)
    blob = vfs.engine.repo.read_blob_at(vfs.engine.repo.head(), CATALOG_REL)
    catalog = json.loads(blob.decode("utf-8"))
    assert rid in catalog["tombstones"]
    assert rid not in catalog["by_id"]
