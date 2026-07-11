"""Observable async push / projection tracking (design §6.5-6.8, INV-9).

Local publish is the ACK boundary (INV-9); remote push and read-model
projections (FTS/graph/L1/INDEX) are asynchronous. This module gives them the
minimal *observable, retryable* contract M1 requires: every committed mutation
records a pending push entry and per-projection checkpoints, and status exposes
freshness (``indexed_through_commit``, ``oldest_pending_age``, generation, lag,
last error, retry state). State is a rebuildable operational mirror under the
reserved ``.kb`` namespace; it is never authoritative content (INV-6).

M1 does not deploy real remotes or index backends; the tracker models the queue
so durability/observability can be tested deterministically.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

_KB_DIR = ".kb"
_STATE_FILE = os.path.join(_KB_DIR, "projection.json")

# The read models M1 knows about (design §6.8). Each tracks its own checkpoint.
PROJECTIONS = ("fts", "graph", "l1", "index")


@dataclass
class PushEntry:
    commit_sha: str
    enqueued_at: float
    attempts: int = 0
    last_error: str | None = None
    pushed: bool = False


@dataclass
class ProjectionState:
    push_queue: list = field(default_factory=list)
    checkpoints: dict = field(default_factory=dict)  # name -> commit_sha
    generations: dict = field(default_factory=dict)  # name -> int
    errors: dict = field(default_factory=dict)       # name -> str|None


class ProjectionTracker:
    """File-backed, deterministic push/projection observability."""

    def __init__(self, repo_root: str, *, now=time.time) -> None:
        self.repo_root = repo_root
        self._now = now
        self._path = os.path.join(repo_root, _STATE_FILE)
        self._state = self._load()

    def _load(self) -> ProjectionState:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    d = json.load(f)
                st = ProjectionState(
                    push_queue=[PushEntry(**e) for e in d.get("push_queue", [])],
                    checkpoints=d.get("checkpoints", {}),
                    generations=d.get("generations", {}),
                    errors=d.get("errors", {}))
                return st
            except (OSError, ValueError, TypeError):
                pass
        return ProjectionState()

    def _save(self) -> None:
        os.makedirs(os.path.join(self.repo_root, _KB_DIR), exist_ok=True)
        d = {
            "push_queue": [asdict(e) for e in self._state.push_queue],
            "checkpoints": self._state.checkpoints,
            "generations": self._state.generations,
            "errors": self._state.errors,
        }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(d, f, sort_keys=True, ensure_ascii=False)

    # ── mutation-time hooks ───────────────────────────────────────────
    def record_commit(self, commit_sha: str, manifest) -> dict:
        """Enqueue a pending push and mark projections as behind this commit."""
        self._state.push_queue.append(
            PushEntry(commit_sha=commit_sha, enqueued_at=self._now()))
        self._save()
        return self.projection_status()

    # ── async workers (deterministic, testable) ───────────────────────
    def push_once(self, *, fail: bool = False) -> dict:
        """Attempt to push the oldest pending commit (fast-forward only).

        ``fail=True`` models a network failure: the attempt is recorded and the
        entry stays pending for retry (exponential backoff is the caller's job).
        """
        pending = [e for e in self._state.push_queue if not e.pushed]
        if not pending:
            return {"pushed": None, "pending": 0}
        entry = pending[0]
        entry.attempts += 1
        if fail:
            entry.last_error = "remote unreachable"
        else:
            entry.pushed = True
            entry.last_error = None
        self._save()
        return {"pushed": None if fail else entry.commit_sha,
                "pending": sum(1 for e in self._state.push_queue if not e.pushed)}

    def apply_projection(self, name: str, commit_sha: str, *,
                         fail: bool = False) -> dict:
        """Advance one projection checkpoint to ``commit_sha`` (or record error).

        A failed generation is discarded (checkpoint unchanged); the worker
        never claims a higher checkpoint after a failed commit (design §6.8).
        """
        if name not in PROJECTIONS:
            raise ValueError(f"unknown projection {name!r}")
        if fail:
            self._state.errors[name] = "projection apply failed"
            self._save()
            return {"name": name, "checkpoint": self._state.checkpoints.get(name),
                    "applied": False}
        self._state.checkpoints[name] = commit_sha
        self._state.generations[name] = self._state.generations.get(name, 0) + 1
        self._state.errors[name] = None
        self._save()
        return {"name": name, "checkpoint": commit_sha, "applied": True}

    # ── observability ─────────────────────────────────────────────────
    def sync_status(self) -> str:
        return "pending" if any(not e.pushed for e in self._state.push_queue) \
            else "synced"

    def oldest_pending_age(self) -> float | None:
        pend = [e for e in self._state.push_queue if not e.pushed]
        if not pend:
            return None
        return self._now() - min(e.enqueued_at for e in pend)

    def projection_status(self) -> dict:
        out = {}
        for name in PROJECTIONS:
            out[name] = {
                "indexed_through_commit": self._state.checkpoints.get(name),
                "generation": self._state.generations.get(name, 0),
                "last_error": self._state.errors.get(name),
            }
        return out

    def status(self, head: str | None) -> dict:
        pend = [e for e in self._state.push_queue if not e.pushed]
        return {
            "canonical_head": head,
            "sync_status": self.sync_status(),
            "push": {
                "pending_commits": len(pend),
                "oldest_pending_age": self.oldest_pending_age(),
                "last_error": (pend[0].last_error if pend else None),
            },
            "projections": self.projection_status(),
        }
