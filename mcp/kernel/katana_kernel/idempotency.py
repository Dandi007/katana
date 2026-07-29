"""Opt-in SQLite mutation ledger for idempotent governed operations.

The ledger is runtime state, not a Git transaction target.  Callers must keep
its database and SQLite sidecar files ignored by Git and coordinate claims with
the repository mutation lock.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


_SCHEMA_VERSION = 1
_UNRESOLVED_STATES = ("PENDING", "PREPARED", "BROKEN", "ORPHANED")


class IdempotencyConflictError(RuntimeError):
    """Raised when one idempotency key is reused for a different request."""

    def __init__(self, mutation_id: str):
        super().__init__(
            f"idempotency key belongs to a different request: {mutation_id}"
        )
        self.mutation_id = mutation_id


class InvalidMutationTransitionError(RuntimeError):
    """Raised when mutation runtime state would move through an unsafe edge."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_request_hash(payload: Any) -> str:
    """Hash a JSON request using the ledger's stable canonical encoding."""
    encoded = _canonical_json(payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _key_hash(domain: str, idempotency_key: str) -> str:
    encoded = (
        "katana-idempotency-v1\0" + domain + "\0" + idempotency_key
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


@dataclass(frozen=True)
class MutationRecord:
    mutation_id: str
    domain: str
    op: str
    key_hash: str
    request_hash: str
    folder_id: str | None
    source_session_id: str | None
    state: str
    base_sha: str
    attempt: int
    changed_paths: list[str]
    postimages: dict[str, str]
    result: dict[str, Any] | None
    commit_sha: str | None
    response: dict[str, Any] | None
    error: Any
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class MutationClaim:
    created: bool
    record: MutationRecord


class SQLiteMutationLedger:
    """Durable per-repository idempotency and mutation lifecycle state."""

    def __init__(self, path: str | Path, *, timeout_seconds: float = 5.0):
        self._path = Path(path)
        self._timeout_seconds = timeout_seconds
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(
            f"PRAGMA busy_timeout={max(0, int(self._timeout_seconds * 1000))}"
        )
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mutation_ledger (
                    mutation_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    op TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    folder_id TEXT,
                    source_session_id TEXT,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'PENDING',
                            'PREPARED',
                            'COMMITTED',
                            'ABORTED',
                            'BROKEN',
                            'ORPHANED',
                            'LEGACY_COMMITTED'
                        )
                    ),
                    base_sha TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt > 0),
                    changed_paths_json TEXT,
                    postimages_json TEXT,
                    result_json TEXT,
                    commit_sha TEXT,
                    response_json TEXT,
                    error_json TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(domain, key_hash)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS mutation_ledger_state_idx
                ON mutation_ledger(state)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO ledger_meta(key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(_SCHEMA_VERSION),),
            )
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> MutationRecord:
        return MutationRecord(
            mutation_id=row["mutation_id"],
            domain=row["domain"],
            op=row["op"],
            key_hash=row["key_hash"],
            request_hash=row["request_hash"],
            folder_id=row["folder_id"],
            source_session_id=row["source_session_id"],
            state=row["state"],
            base_sha=row["base_sha"],
            attempt=row["attempt"],
            changed_paths=_load_json(row["changed_paths_json"], []),
            postimages=_load_json(row["postimages_json"], {}),
            result=_load_json(row["result_json"], None),
            commit_sha=row["commit_sha"],
            response=_load_json(row["response_json"], None),
            error=_load_json(row["error_json"], None),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _select(
        connection: sqlite3.Connection, mutation_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM mutation_ledger WHERE mutation_id = ?",
            (mutation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown mutation: {mutation_id}")
        return row

    def claim(
        self,
        *,
        domain: str,
        op: str,
        idempotency_key: str,
        request_hash: str,
        base_sha: str,
        folder_id: str | None = None,
        source_session_id: str | None = None,
    ) -> MutationClaim:
        if not domain or not op or not idempotency_key or not request_hash:
            raise ValueError(
                "domain, op, idempotency_key, and request_hash are required"
            )
        hashed_key = _key_hash(domain, idempotency_key)
        now = time.time_ns()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM mutation_ledger
                WHERE domain = ? AND key_hash = ?
                """,
                (domain, hashed_key),
            ).fetchone()
            if row is None:
                mutation_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO mutation_ledger(
                        mutation_id, domain, op, key_hash, request_hash,
                        folder_id, source_session_id, state, base_sha,
                        attempt, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, 1, ?, ?)
                    """,
                    (
                        mutation_id,
                        domain,
                        op,
                        hashed_key,
                        request_hash,
                        folder_id,
                        source_session_id,
                        base_sha,
                        now,
                        now,
                    ),
                )
                return MutationClaim(
                    True, self._record(self._select(connection, mutation_id))
                )

            if row["op"] != op or row["request_hash"] != request_hash:
                raise IdempotencyConflictError(row["mutation_id"])

            if row["state"] == "ABORTED":
                connection.execute(
                    """
                    UPDATE mutation_ledger
                    SET state = 'PENDING',
                        base_sha = ?,
                        attempt = attempt + 1,
                        changed_paths_json = NULL,
                        postimages_json = NULL,
                        result_json = NULL,
                        commit_sha = NULL,
                        response_json = NULL,
                        error_json = NULL,
                        updated_at = ?
                    WHERE mutation_id = ?
                    """,
                    (base_sha, now, row["mutation_id"]),
                )
                return MutationClaim(
                    True,
                    self._record(self._select(connection, row["mutation_id"])),
                )

            return MutationClaim(False, self._record(row))

    def get(self, mutation_id: str) -> MutationRecord:
        connection = self._connect()
        try:
            return self._record(self._select(connection, mutation_id))
        finally:
            connection.close()

    def prepare(
        self,
        mutation_id: str,
        *,
        result: dict[str, Any],
        changed_paths: list[str],
        postimages: dict[str, str],
    ) -> MutationRecord:
        return self._transition(
            mutation_id,
            allowed={"PENDING"},
            target="PREPARED",
            assignments={
                "result_json": _canonical_json(result),
                "changed_paths_json": _canonical_json(changed_paths),
                "postimages_json": _canonical_json(postimages),
                "error_json": None,
            },
        )

    def finalize(
        self,
        mutation_id: str,
        *,
        commit_sha: str,
        response: dict[str, Any],
    ) -> MutationRecord:
        if not commit_sha:
            raise ValueError("commit_sha is required")
        return self._transition(
            mutation_id,
            allowed={"PREPARED"},
            target="COMMITTED",
            assignments={
                "commit_sha": commit_sha,
                "response_json": _canonical_json(response),
                "error_json": None,
            },
        )

    def mark_aborted(self, mutation_id: str, reason: Any) -> MutationRecord:
        return self._transition(
            mutation_id,
            allowed={"PENDING"},
            target="ABORTED",
            assignments={"error_json": _canonical_json(reason)},
        )

    def mark_broken(self, mutation_id: str, evidence: Any) -> MutationRecord:
        return self._transition(
            mutation_id,
            allowed={"PENDING", "PREPARED"},
            target="BROKEN",
            assignments={"error_json": _canonical_json(evidence)},
        )

    def mark_orphaned(self, mutation_id: str, evidence: Any) -> MutationRecord:
        return self._transition(
            mutation_id,
            allowed={"COMMITTED"},
            target="ORPHANED",
            assignments={"error_json": _canonical_json(evidence)},
        )

    def _transition(
        self,
        mutation_id: str,
        *,
        allowed: set[str],
        target: str,
        assignments: dict[str, Any],
    ) -> MutationRecord:
        now = time.time_ns()
        with self._transaction() as connection:
            current = self._select(connection, mutation_id)
            if current["state"] not in allowed:
                expected = ", ".join(sorted(allowed))
                raise InvalidMutationTransitionError(
                    f"cannot transition {current['state']} to {target}; "
                    f"expected one of: {expected}"
                )
            columns = [f"{column} = ?" for column in assignments]
            values = list(assignments.values())
            columns.extend(["state = ?", "updated_at = ?"])
            values.extend([target, now, mutation_id])
            connection.execute(
                f"""
                UPDATE mutation_ledger
                SET {", ".join(columns)}
                WHERE mutation_id = ?
                """,
                values,
            )
            return self._record(self._select(connection, mutation_id))

    def list_unresolved(self) -> list[MutationRecord]:
        placeholders = ", ".join("?" for _ in _UNRESOLVED_STATES)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT * FROM mutation_ledger
                WHERE state IN ({placeholders})
                ORDER BY created_at, mutation_id
                """,
                _UNRESOLVED_STATES,
            ).fetchall()
            return [self._record(row) for row in rows]
        finally:
            connection.close()

    @property
    def path(self) -> str:
        return str(self._path)
