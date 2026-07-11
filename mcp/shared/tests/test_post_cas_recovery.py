"""Post-CAS forward-recovery anchors (design §6.6, operator P0 #4).

The protected-ref CAS is the single linearization point. After a crash at any
point PAST the ref update — mid-materialize, before outbox enqueue, before
receipt persist — restart must forward-recover deterministically:
  - the working tree is recalibrated to HEAD,
  - the same mutation replays to the original commit with no second effect,
  - the push backlog is rebuilt from committed manifests.
"""
import json
import os

import pytest

from katana_kb_mcp_shared import kernel
from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import KernelError


def _engine(tmp_path):
    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    eng.repo.ensure_repo()
    return eng


def _create(rid, path, body, mid=None, rh=None):
    b = MutationBatch(domain="test", mutation_id=mid, request_hash=rh)
    b.add(Change(op=Op.CREATE, resource_id=rid, after_path=path,
                 after_content=body))
    return b


def test_crash_after_ref_before_materialize_recalibrates(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    # Simulate crash: publish advances the ref, but materialize never runs.
    real_materialize = eng.repo.materialize

    def crash(*a, **k):
        raise RuntimeError("crash between publish and materialize")

    monkeypatch.setattr(eng.repo, "materialize", crash)
    with pytest.raises(RuntimeError):
        eng.commit(_create("t-1", "a.md", b"hello\n"), message="create")
    # The ref advanced (commit is durable) but the working tree is behind.
    head = eng.repo.head()
    assert head is not None
    assert not (tmp_path / "a.md").exists()

    # Restart: reconcile recalibrates the working tree to HEAD.
    monkeypatch.setattr(eng.repo, "materialize", real_materialize)
    fresh = kernel.TransactionEngine(str(tmp_path), domain="test")
    out = fresh.reconcile()
    assert out["recalibrated"] is True
    assert (tmp_path / "a.md").read_text() == "hello\n"
    assert not fresh.repo.is_dirty()


def test_crash_before_receipt_persist_replays_to_same_commit(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    # Crash right before the receipt is persisted (after ref + materialize).
    monkeypatch.setattr(eng, "_store_receipt",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        eng.commit(_create("t-1", "a.md", b"hi\n", mid="m1", rh="h1"),
                   message="create")
    head = eng.repo.head()
    # Restart and reconcile; the mutation replays to the SAME commit, no second effect.
    fresh = kernel.TransactionEngine(str(tmp_path), domain="test")
    fresh.reconcile()
    r = fresh.commit(_create("t-1", "a.md", b"OVERWRITE\n", mid="m1", rh="h1"),
                     message="replay")
    assert r.commit_sha == head
    assert (tmp_path / "a.md").read_text() == "hi\n"


def test_crash_before_outbox_rebuilds_backlog(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    # Two committed mutations; drop the operational projection mirror entirely.
    eng.commit(_create("t-1", "a.md", b"a\n"), message="c1")
    eng.commit(_create("t-2", "b.md", b"b\n"), message="c2")
    state = tmp_path / ".kb" / "projection.json"
    if state.exists():
        state.unlink()
    # Restart: reconcile rebuilds the push backlog from committed manifests.
    fresh = kernel.TransactionEngine(str(tmp_path), domain="test")
    fresh.reconcile()
    st = fresh.status()
    assert st["push"]["pending_commits"] == 2


def test_reconcile_is_idempotent(tmp_path):
    eng = _engine(tmp_path)
    eng.commit(_create("t-1", "a.md", b"a\n", mid="m1", rh="h1"), message="c1")
    fresh = kernel.TransactionEngine(str(tmp_path), domain="test")
    out1 = fresh.reconcile()
    out2 = fresh.reconcile()
    assert out1["reconciled"] == out2["reconciled"]
    # Backlog is not duplicated across repeated reconciles.
    assert fresh.status()["push"]["pending_commits"] == 1
