"""Stable kernel error envelope (design §5.9).

Every mutation/read error carries a machine-stable ``code`` plus optional
identity/revision context. Errors NEVER accompany a partially-visible mutation:
the transaction engine only ever commits an all-or-nothing MutationBatch, so a
raised ``KernelError`` always means zero canonical delta.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Concurrency / identity
REVISION_CONFLICT = "REVISION_CONFLICT"
BASE_COMMIT_CONFLICT = "BASE_COMMIT_CONFLICT"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
RESOURCE_REPLACED = "RESOURCE_REPLACED"
REF_MISMATCH = "REF_MISMATCH"
REPOSITORY_EPOCH_CHANGED = "REPOSITORY_EPOCH_CHANGED"

# Boundary / policy
POLICY_VIOLATION = "POLICY_VIOLATION"
INVALID_CONTENT = "INVALID_CONTENT"
QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
INVALID_PATH = "INVALID_PATH"
EXDEV = "EXDEV"
OPERATION_REQUIRES_MAINTENANCE = "OPERATION_REQUIRES_MAINTENANCE"

# Not-found / lookup
NOT_FOUND = "NOT_FOUND"

# Durability / readiness
WRITER_UNAVAILABLE = "WRITER_UNAVAILABLE"
QUEUE_FULL = "QUEUE_FULL"
COMMIT_FAILED = "COMMIT_FAILED"
MANIFEST_INVALID = "MANIFEST_INVALID"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
REMOTE_DIVERGED = "REMOTE_DIVERGED"
SYNC_BACKPRESSURE = "SYNC_BACKPRESSURE"
INDEX_BEHIND = "INDEX_BEHIND"

# Accepted no-op is not an error, but shares the envelope vocabulary.
NO_CHANGE = "NO_CHANGE"

_RETRYABLE = {
    REVISION_CONFLICT, BASE_COMMIT_CONFLICT, WRITER_UNAVAILABLE,
    QUEUE_FULL, COMMIT_FAILED, SYNC_BACKPRESSURE, INDEX_BEHIND,
}


@dataclass
class KernelError(Exception):
    """Structured, transport-agnostic kernel error."""

    code: str
    message: str
    resource_id: str | None = None
    virtual_path: str | None = None
    repository_epoch: int | None = None
    expected_revision: str | None = None
    actual_revision: str | None = None
    current_commit: str | None = None
    violations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__(f"{self.code}: {self.message}")

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    def to_envelope(self) -> dict:
        env: dict = {
            "code": self.code,
            "message": self.message,
            "violations": list(self.violations),
            "retryable": self.retryable,
        }
        for k in ("resource_id", "virtual_path", "repository_epoch",
                  "expected_revision", "actual_revision", "current_commit"):
            v = getattr(self, k)
            if v is not None:
                env[k] = v
        return env
