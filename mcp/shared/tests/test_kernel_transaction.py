"""Git-native transaction protocol tests (design §6): CAS, idempotency, no-op,
crash/forward-recovery. These are the anti-regression anchors for durability.
"""
import os

import pytest

from katana_kb_mcp_shared import kernel
from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import (
    BASE_COMMIT_CONFLICT,
    IDEMPOTENCY_CONFLICT,
    KernelError,
)
from katana_kb_mcp_shared.kernel.manifest import extract_from_message


@pytest.fixture
def engine(tmp_path):
    return kernel.TransactionEngine(str(tmp_path), domain="test")


def _create(rid, path, body):
    b = MutationBatch(domain="test")
    b.add(Change(op=Op.CREATE, resource_id=rid, after_path=path,
                 after_content=body))
    return b


def test_commit_publishes_and_writes_tree(engine, tmp_path):
    r = engine.commit(_create("t-1", "a.md", b"hello\n"), message="create")
    assert r.committed
    assert r.commit_sha
    assert (tmp_path / "a.md").read_text() == "hello\n"


def test_manifest_embedded_in_commit(engine):
    r = engine.commit(_create("t-1", "a.md", b"hi\n"), message="create")
    msg = engine.repo.show_message(r.commit_sha)
    manifest = extract_from_message(msg)
    assert manifest is not None
    assert manifest.domain == "test"
    assert manifest.changes[0]["resource_id"] == "t-1"


def test_empty_batch_is_accepted_no_op(engine):
    r = engine.commit(MutationBatch(domain="test"), message="noop")
    assert r.no_change
    assert not r.committed
    assert r.receipt["code"] == "NO_CHANGE"


def test_lost_response_replay_returns_original_receipt(engine, tmp_path):
    b = _create("t-1", "a.md", b"hello\n")
    b.mutation_id, b.request_hash = "mut-1", "h1"
    r1 = engine.commit(b, message="create")

    b2 = _create("t-1", "a.md", b"OVERWRITE\n")
    b2.mutation_id, b2.request_hash = "mut-1", "h1"
    r2 = engine.commit(b2, message="replay")
    # Exactly-once committed effect: same commit, original content preserved.
    assert r2.commit_sha == r1.commit_sha
    assert (tmp_path / "a.md").read_text() == "hello\n"


def test_same_id_different_hash_is_conflict(engine):
    b = _create("t-1", "a.md", b"hello\n")
    b.mutation_id, b.request_hash = "mut-1", "h1"
    engine.commit(b, message="create")

    b2 = _create("t-1", "a.md", b"x\n")
    b2.mutation_id, b2.request_hash = "mut-1", "h2"
    with pytest.raises(KernelError) as ei:
        engine.commit(b2, message="conflict")
    assert ei.value.code == IDEMPOTENCY_CONFLICT


def test_stale_base_commit_is_cas_conflict(engine):
    r = engine.commit(_create("t-1", "a.md", b"hello\n"), message="create")
    base = r.commit_sha
    engine.commit(_create("t-2", "b.md", b"world\n"), message="create b")

    b = MutationBatch(domain="test", expected_base_commit=base)
    b.add(Change(op=Op.WRITE, resource_id="t-1", after_path="a.md",
                 after_content=b"changed\n"))
    with pytest.raises(KernelError) as ei:
        engine.commit(b, message="stale")
    assert ei.value.code == BASE_COMMIT_CONFLICT
    assert ei.value.retryable


def test_rename_keeps_id_moves_path(engine, tmp_path):
    engine.commit(_create("t-1", "a.md", b"hello\n"), message="create")
    b = MutationBatch(domain="test")
    b.add(Change(op=Op.RENAME, resource_id="t-1",
                 before_path="a.md", after_path="sub/c.md"))
    engine.commit(b, message="rename")
    assert (tmp_path / "sub" / "c.md").exists()
    assert not (tmp_path / "a.md").exists()


def test_delete_removes_file(engine, tmp_path):
    engine.commit(_create("t-1", "a.md", b"hello\n"), message="create")
    b = MutationBatch(domain="test")
    b.add(Change(op=Op.DELETE, resource_id="t-1", before_path="a.md"))
    engine.commit(b, message="delete")
    assert not (tmp_path / "a.md").exists()


def test_reconcile_rebuilds_receipts_from_history(tmp_path):
    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    b = _create("t-1", "a.md", b"hello\n")
    b.mutation_id, b.request_hash = "mut-1", "h1"
    eng.commit(b, message="create")

    # Simulate operational-mirror loss (DB全失, design §6.6) and rebuild.
    receipts = tmp_path / ".kb" / "receipts.json"
    if receipts.exists():
        receipts.unlink()
    fresh = kernel.TransactionEngine(str(tmp_path), domain="test")
    out = fresh.reconcile()
    assert out["reconciled"] >= 1
    # A subsequent replay of the same mutation is still idempotent.
    b2 = _create("t-1", "a.md", b"X\n")
    b2.mutation_id, b2.request_hash = "mut-1", "h1"
    r = fresh.commit(b2, message="replay")
    assert (tmp_path / "a.md").read_text() == "hello\n"
    assert r.committed


def test_partial_failure_leaves_no_visible_effect(tmp_path):
    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    r = eng.commit(_create("t-1", "a.md", b"hello\n"), message="create")
    head = r.commit_sha

    # A CAS conflict must not advance the canonical ref nor leave a commit.
    b = MutationBatch(domain="test", expected_base_commit="deadbeef" * 5)
    b.add(Change(op=Op.WRITE, resource_id="t-1", after_path="a.md",
                 after_content=b"x\n"))
    with pytest.raises(KernelError):
        eng.commit(b, message="doomed")
    assert eng.repo.head() == head


def test_receipt_carries_async_projection_status(engine):
    r = engine.commit(_create("t-1", "a.md", b"hi\n"), message="create")
    # Async fields are present and observable (design §6.5).
    assert r.receipt["sync_status"] == "pending"
    assert "fts" in r.receipt["projection_status"]
    assert r.receipt["projection_status"]["fts"]["indexed_through_commit"] is None


def test_staged_projection_leaves_no_visible_effect_on_publish_failure(tmp_path, monkeypatch):
    """A domain tool's writer-private projection leaves zero visible effect on
    publish failure (design §6.6, operator P0 #2).

    Governed writers project into a private staging dir, never the canonical
    working tree, so a failed publish needs no working-tree rollback: HEAD is
    unchanged, the canonical tree is untouched and clean, and no partial file
    ever appears."""
    from katana_kb_mcp_shared.kernel.catalog import Catalog
    from katana_kb_mcp_shared.kernel.facade import GovernedVFS

    class _P:
        domain = "test"; id_prefix = "t-"; policy_version = 1
        def validate(self, batch):
            return None

    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    eng.repo.ensure_repo()
    vfs = GovernedVFS(eng, Catalog(str(tmp_path), id_prefix="t-"), _P())
    vfs.fs_create(virtual_path="a.md", content="hello\n")
    head = eng.repo.head()

    def boom(*a, **k):
        raise KernelError("COMMIT_FAILED", "simulated publish failure")

    monkeypatch.setattr(eng.repo, "publish", boom)
    with pytest.raises(KernelError):
        with vfs.staging() as stg:
            # Domain tool writes the post-state into PRIVATE staging.
            (__import__("pathlib").Path(stg.root) / "b.md").write_text(
                "domain-written\n", encoding="utf-8")
            vfs.commit_staged(stg, message="doomed", writes=["b.md"])

    assert eng.repo.head() == head
    assert not (tmp_path / "b.md").exists()
    assert not eng.repo.is_dirty()
