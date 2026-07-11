"""Transaction manifest + receipt schema (design §6.2, §6.5).

The manifest is the reserved, replayable transaction record embedded in the
commit trailer. It carries idempotency keys, minimal audit context, the change
set and replayable projection events. Tokens / secrets / full sensitive bodies
never enter the manifest.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

PROTOCOL_VERSION = 1
SCHEMA_VERSION = 1
TRAILER_KEY = "KB-Manifest"


@dataclass
class Manifest:
    protocol_version: int
    schema_version: int
    policy_version: int
    repository_epoch: int
    domain: str
    tenant: str
    principal_id: str
    scopes: list[str]
    mutation_id: str | None
    request_hash: str | None
    base_commit: str | None
    changes: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    projection_events: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Manifest":
        return cls(**json.loads(raw))


def encode_trailer(manifest: Manifest) -> str:
    """Single-line commit trailer that survives round-trips."""
    return f"{TRAILER_KEY}: {manifest.to_json()}"


def extract_from_message(message: str) -> Manifest | None:
    """Recover a manifest from a commit message (forward recovery, §6.3)."""
    for line in message.splitlines():
        if line.startswith(TRAILER_KEY + ":"):
            raw = line[len(TRAILER_KEY) + 1:].strip()
            try:
                return Manifest.from_json(raw)
            except (ValueError, TypeError):
                return None
    return None


def build_receipt(manifest: Manifest, commit_sha: str, *,
                  sync_status: str = "pending",
                  projection_status: dict | None = None) -> dict:
    """Success receipt returned to the client (§6.5)."""
    return {
        "mutation_id": manifest.mutation_id,
        "request_hash": manifest.request_hash,
        "repository_epoch": manifest.repository_epoch,
        "base_commit": manifest.base_commit,
        "commit_sha": commit_sha,
        "changes": manifest.changes,
        "policy_version": manifest.policy_version,
        "schema_version": manifest.schema_version,
        "warnings": manifest.warnings,
        "sync_status": sync_status,
        "projection_status": projection_status or {},
    }
