"""MutationBatch — the single write representation (design §4.4, §5.5, INV-5).

Both domain tools and the governed ``fs_*`` façade compile into exactly one
MutationBatch; neither implements its own write path. A batch is single-repo and
all-or-nothing (INV-8). The projected post-state is built in writer-private
staging and only becomes canonical when the Git ref CAS succeeds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Op(str, Enum):
    CREATE = "create"
    WRITE = "write"
    EDIT = "edit"
    RENAME = "rename"
    COPY = "copy"
    DELETE = "delete"
    MKDIR = "mkdir"


@dataclass
class Change:
    """One resource-level change within a batch.

    ``after_content`` is None for deletes. ``before_*`` fields are the CAS base
    captured at resolve time; the engine records before/after in the manifest.
    """
    op: Op
    resource_id: str
    after_path: str | None = None
    before_path: str | None = None
    after_content: bytes | None = None
    before_content: bytes | None = None
    before_revision: str | None = None
    after_revision: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None

    def to_manifest_entry(self) -> dict:
        return {
            "op": self.op.value,
            "resource_id": self.resource_id,
            "before_path": self.before_path,
            "after_path": self.after_path,
            "before_revision": self.before_revision,
            "after_revision": self.after_revision,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
        }


@dataclass
class MutationBatch:
    """A single-repo, all-or-nothing set of changes plus provenance."""

    domain: str
    tenant: str = "default"
    changes: list[Change] = field(default_factory=list)
    mutation_id: str | None = None
    request_hash: str | None = None
    expected_base_commit: str | None = None
    principal_id: str = "local"
    scopes: list[str] = field(default_factory=lambda: ["mutate"])
    warnings: list[str] = field(default_factory=list)
    projection_events: list[dict] = field(default_factory=list)
    # Server-managed canonical files (identity/link catalogs) committed
    # ATOMICALLY with content in the same transaction (design 6.1/6.2, INV-6).
    # Maps a reserved-namespace path -> bytes (or None to delete). Never policy-
    # validated as domain content and never listed in manifest changes[].
    reserved: dict = field(default_factory=dict)
    # True when a domain tool already projected the post-state into the working
    # tree; the engine then validates+publishes rather than re-projecting.
    already_materialized: bool = False

    def add(self, change: Change) -> "MutationBatch":
        self.changes.append(change)
        return self

    def add_reserved(self, path: str, content: bytes | None) -> "MutationBatch":
        self.reserved[path] = content
        return self

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def touched_paths(self) -> list[str]:
        paths: list[str] = []
        for c in self.changes:
            for p in (c.before_path, c.after_path):
                if p and p not in paths:
                    paths.append(p)
        for p in self.reserved:
            if p not in paths:
                paths.append(p)
        return paths
