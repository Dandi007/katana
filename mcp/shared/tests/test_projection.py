"""Observable async push / projection anchors (design §6.5-6.8, INV-9).

Local publish is the ACK boundary; remote push and read-model projections are
async but MUST be observable, retryable, and expose freshness/checkpoint. These
tests exercise the ProjectionTracker the TransactionEngine wires into every
mutation receipt and the fs_status/status endpoints.
"""
import pytest

from katana_kb_mcp_shared import kernel
from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.projection import ProjectionTracker


def _engine(tmp_path):
    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    eng.repo.ensure_repo()
    return eng


def _create(eng, rid, path, body):
    b = MutationBatch(domain="test")
    b.add(Change(op=Op.CREATE, resource_id=rid, after_path=path,
                 after_content=body))
    return eng.commit(b, message="create")


def test_commit_enqueues_pending_push(tmp_path):
    eng = _engine(tmp_path)
    r = _create(eng, "t-1", "a.md", b"x\n")
    assert r.receipt["sync_status"] == "pending"
    st = eng.status()
    assert st["push"]["pending_commits"] == 1
    assert st["sync_status"] == "pending"


def test_push_retryable_failure_then_success(tmp_path):
    t = ProjectionTracker(str(tmp_path), now=lambda: 100.0)

    class _M:  # minimal manifest stand-in
        pass
    t.record_commit("c1", _M())
    # A network failure keeps the entry pending and records the error (retry).
    out = t.push_once(fail=True)
    assert out["pushed"] is None
    assert out["pending"] == 1
    assert t.status(None)["push"]["last_error"] == "remote unreachable"
    # Retry succeeds → drains the queue.
    out2 = t.push_once()
    assert out2["pushed"] == "c1"
    assert t.sync_status() == "synced"


def test_projection_checkpoint_and_freshness(tmp_path):
    t = ProjectionTracker(str(tmp_path))

    class _M:
        pass
    t.record_commit("c1", _M())
    # Behind: no checkpoint yet.
    assert t.projection_status()["fts"]["indexed_through_commit"] is None
    # A failed generation does NOT advance the checkpoint (design §6.8).
    t.apply_projection("fts", "c1", fail=True)
    assert t.projection_status()["fts"]["indexed_through_commit"] is None
    assert t.projection_status()["fts"]["last_error"]
    # A successful apply advances the checkpoint and bumps the generation.
    t.apply_projection("fts", "c1")
    ps = t.projection_status()["fts"]
    assert ps["indexed_through_commit"] == "c1"
    assert ps["generation"] == 1
    assert ps["last_error"] is None


def test_oldest_pending_age_observable(tmp_path):
    clock = {"t": 0.0}
    t = ProjectionTracker(str(tmp_path), now=lambda: clock["t"])

    class _M:
        pass
    t.record_commit("c1", _M())
    clock["t"] = 42.0
    assert t.oldest_pending_age() == 42.0
    t.push_once()
    assert t.oldest_pending_age() is None


def test_status_survives_reload(tmp_path):
    t = ProjectionTracker(str(tmp_path))

    class _M:
        pass
    t.record_commit("c1", _M())
    t.apply_projection("graph", "c1")
    # State is a rebuildable operational mirror persisted under .kb.
    t2 = ProjectionTracker(str(tmp_path))
    assert t2.projection_status()["graph"]["indexed_through_commit"] == "c1"
    assert t2.sync_status() == "pending"
