"""DomainPolicy: per-domain op allowlist + invariants."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class PolicyViolationError(Exception):
    """Raised when a domain policy invariant is violated."""


class DomainPolicy:
    def __init__(
        self,
        domain: str,
        allowed_ops: set[str],
        invariants: list[Callable[[str, str, dict[str, Any]], None]] | None = None,
    ):
        self._domain = domain
        self._allowed_ops = allowed_ops
        self._invariants = invariants or []

    def verify(self, op: str, args: dict[str, Any]) -> None:
        if op not in self._allowed_ops:
            raise PolicyViolationError(f"op {op!r} not allowed for domain {self._domain!r}")
        for inv in self._invariants:
            inv(self._domain, op, args)

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def allowed_ops(self) -> set[str]:
        return set(self._allowed_ops)