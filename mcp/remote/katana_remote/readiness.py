"""Readiness probes: livez (no auth), read_ready, write_ready (authenticated).

Design §7.5:
- livez: process/event-loop alive, no content state leaked
- read_ready: auth registry available, canonical ref/tree/object readable
- write_ready: single-writer fence, startup reconciliation, schema write compat,
  disk reserve, queue/divergence allows mutation
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReadinessState:
    live: bool = True
    read_ready: bool = True
    write_ready: bool = True
    writer_fence: bool = True
    schema_compatible: bool = True
    disk_reserve_ok: bool = True
    reconciliation_done: bool = True
    details: dict[str, str] = field(default_factory=dict)


class ReadinessService:
    def __init__(self) -> None:
        self._state = ReadinessState()

    def livez(self) -> dict:
        return {"status": "ok" if self._state.live else "degraded"}

    def read_ready(self) -> dict:
        return {
            "status": "ok" if self._state.read_ready else "not_ready",
            "auth_registry": "available" if self._state.read_ready else "unavailable",
            "canonical_readable": self._state.read_ready,
            "schema_read_compat": self._state.schema_compatible,
        }

    def write_ready(self) -> dict:
        return {
            "status": "ok" if self._state.write_ready else "not_ready",
            "writer_fence": self._state.writer_fence,
            "reconciliation": "complete" if self._state.reconciliation_done else "in_progress",
            "schema_write_compat": self._state.schema_compatible,
            "disk_reserve": "ok" if self._state.disk_reserve_ok else "low",
            "mutation_allowed": self._state.write_ready,
        }

    def set_writer_fence(self, value: bool) -> None:
        self._state.writer_fence = value
        self._update_write_ready()

    def set_schema_compatible(self, value: bool) -> None:
        self._state.schema_compatible = value
        self._update_read_ready()
        self._update_write_ready()

    def set_disk_reserve(self, ok: bool) -> None:
        self._state.disk_reserve_ok = ok
        self._update_write_ready()

    def set_reconciliation_done(self, done: bool) -> None:
        self._state.reconciliation_done = done
        self._update_write_ready()

    def _update_read_ready(self) -> None:
        self._state.read_ready = self._state.schema_compatible

    def _update_write_ready(self) -> None:
        self._state.write_ready = (
            self._state.writer_fence
            and self._state.schema_compatible
            and self._state.disk_reserve_ok
            and self._state.reconciliation_done
        )


def livez(service: ReadinessService) -> dict:
    return service.livez()


def read_ready(service: ReadinessService) -> dict:
    return service.read_ready()


def write_ready(service: ReadinessService) -> dict:
    return service.write_ready()