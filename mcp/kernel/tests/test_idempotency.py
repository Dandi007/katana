"""Tests for the opt-in SQLite mutation/idempotency ledger."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from katana_kernel.idempotency import (
    IdempotencyConflictError,
    InvalidMutationTransitionError,
    SQLiteMutationLedger,
    canonical_request_hash,
)


def _request_hash(detail: str = "append progress") -> str:
    return canonical_request_hash(
        {
            "schema_version": 1,
            "folder_id": "wf-abc123",
            "entry": {"action": "session-harvest", "detail": detail},
        }
    )


def _claim(ledger: SQLiteMutationLedger, *, detail: str = "append progress"):
    return ledger.claim(
        domain="work-folder",
        op="wf_append_progress",
        idempotency_key="session-1:lines-10-20",
        request_hash=_request_hash(detail),
        base_sha="a" * 40,
        folder_id="wf-abc123",
        source_session_id="session-1",
    )


def test_canonical_request_hash_is_stable_for_mapping_order():
    first = canonical_request_hash(
        {"folder_id": "wf-abc123", "entry": {"detail": "x", "action": "a"}}
    )
    second = canonical_request_hash(
        {"entry": {"action": "a", "detail": "x"}, "folder_id": "wf-abc123"}
    )

    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_claim_is_persistent_and_does_not_store_raw_key(tmp_path):
    path = tmp_path / ".katana" / "ledger.sqlite"
    ledger = SQLiteMutationLedger(path)

    first = _claim(ledger)
    reopened = SQLiteMutationLedger(path)
    replay = _claim(reopened)

    assert first.created is True
    assert first.record.state == "PENDING"
    assert replay.created is False
    assert replay.record.mutation_id == first.record.mutation_id
    assert replay.record.key_hash != "session-1:lines-10-20"

    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT key_hash FROM mutation_ledger"
        ).fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert stored == first.record.key_hash
    assert journal_mode.lower() == "wal"


def test_concurrent_same_key_claim_has_one_winner(tmp_path):
    ledger = SQLiteMutationLedger(tmp_path / ".katana" / "ledger.sqlite")
    barrier = threading.Barrier(8)

    def claim_once():
        barrier.wait(timeout=5)
        return _claim(ledger)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: claim_once(), range(8)))

    assert sum(result.created for result in results) == 1
    assert len({result.record.mutation_id for result in results}) == 1
    assert {result.record.state for result in results} == {"PENDING"}


def test_same_key_with_different_payload_conflicts_without_mutation(tmp_path):
    ledger = SQLiteMutationLedger(tmp_path / ".katana" / "ledger.sqlite")
    original = _claim(ledger)

    with pytest.raises(IdempotencyConflictError) as exc_info:
        _claim(ledger, detail="different payload")

    assert exc_info.value.mutation_id == original.record.mutation_id
    assert ledger.get(original.record.mutation_id).request_hash == _request_hash()
    assert ledger.get(original.record.mutation_id).state == "PENDING"


def test_prepare_finalize_and_committed_replay(tmp_path):
    ledger = SQLiteMutationLedger(tmp_path / ".katana" / "ledger.sqlite")
    claim = _claim(ledger)

    prepared = ledger.prepare(
        claim.record.mutation_id,
        result={"folder_id": "wf-abc123"},
        changed_paths=["wf-abc123/progress.md", "wf-abc123/_brief.md"],
        postimages={
            "wf-abc123/progress.md": "sha256:" + "1" * 64,
            "wf-abc123/_brief.md": "sha256:" + "2" * 64,
        },
    )
    committed = ledger.finalize(
        claim.record.mutation_id,
        commit_sha="b" * 40,
        response={
            "folder_id": "wf-abc123",
            "commit": "b" * 40,
            "written": ["progress.md", "_brief.md"],
        },
    )
    replay = _claim(ledger)

    assert prepared.state == "PREPARED"
    assert prepared.changed_paths == [
        "wf-abc123/progress.md",
        "wf-abc123/_brief.md",
    ]
    assert committed.state == "COMMITTED"
    assert committed.commit_sha == "b" * 40
    assert committed.response["folder_id"] == "wf-abc123"
    assert replay.created is False
    assert replay.record.state == "COMMITTED"
    assert replay.record.response == committed.response
    assert ledger.list_unresolved() == []


def test_finalize_before_prepare_is_rejected(tmp_path):
    ledger = SQLiteMutationLedger(tmp_path / ".katana" / "ledger.sqlite")
    claim = _claim(ledger)

    with pytest.raises(InvalidMutationTransitionError, match="PENDING"):
        ledger.finalize(
            claim.record.mutation_id,
            commit_sha="b" * 40,
            response={"ok": True},
        )

    assert ledger.get(claim.record.mutation_id).state == "PENDING"


def test_aborted_claim_can_only_be_reclaimed_by_same_payload(tmp_path):
    ledger = SQLiteMutationLedger(tmp_path / ".katana" / "ledger.sqlite")
    claim = _claim(ledger)
    aborted = ledger.mark_aborted(claim.record.mutation_id, "safe pre-write abort")

    assert aborted.state == "ABORTED"

    reclaimed = _claim(ledger)
    assert reclaimed.created is True
    assert reclaimed.record.mutation_id == claim.record.mutation_id
    assert reclaimed.record.state == "PENDING"
    assert reclaimed.record.attempt == 2

    ledger.mark_aborted(claim.record.mutation_id, "safe pre-write abort again")
    with pytest.raises(IdempotencyConflictError):
        _claim(ledger, detail="changed after abort")


def test_broken_and_orphaned_records_remain_unresolved(tmp_path):
    ledger = SQLiteMutationLedger(tmp_path / ".katana" / "ledger.sqlite")
    first = _claim(ledger)
    broken = ledger.mark_broken(first.record.mutation_id, {"detail": "dirty scene"})

    second = ledger.claim(
        domain="work-folder",
        op="wf_append_progress",
        idempotency_key="session-2:lines-1-2",
        request_hash=_request_hash("second"),
        base_sha="b" * 40,
        folder_id="wf-abc123",
        source_session_id="session-2",
    )
    ledger.prepare(
        second.record.mutation_id,
        result={"folder_id": "wf-abc123"},
        changed_paths=["wf-abc123/progress.md"],
        postimages={"wf-abc123/progress.md": "sha256:" + "3" * 64},
    )
    ledger.finalize(
        second.record.mutation_id,
        commit_sha="c" * 40,
        response={"folder_id": "wf-abc123", "commit": "c" * 40},
    )
    orphaned = ledger.mark_orphaned(
        second.record.mutation_id,
        {"detail": "commit is no longer reachable"},
    )

    assert broken.state == "BROKEN"
    assert orphaned.state == "ORPHANED"
    assert {record.state for record in ledger.list_unresolved()} == {
        "BROKEN",
        "ORPHANED",
    }
