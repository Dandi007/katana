"""Stable resource identity + revision/CAS tokens (design §5.3, INV-3).

- ``resource_id`` is a server-minted, immutable, opaque, never-reused logical
  identity. A per-domain prefix (``m-``/``w-``/``wf-`` …) namespaces it.
- ``virtual_path`` is mutable; renames keep the id and only move the path.
- ``resource_revision`` is a CAS token that changes when content, metadata OR
  path change; ``content_revision`` tracks body only. ``commit_sha`` identifies
  a single-repo transaction and never carries identity.
"""
from __future__ import annotations

import hashlib
import secrets


def mint_id(prefix: str, existing: set[str], *, nbytes: int = 3) -> str:
    """Mint a fresh opaque id ``<prefix><hex>`` not present in ``existing``."""
    while True:
        rid = f"{prefix}{secrets.token_hex(nbytes)}"
        if rid not in existing:
            return rid


def content_hash(data: bytes | str) -> str:
    """Stable SHA-256 content hash (design read/receipt field)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def content_revision(content: bytes | str) -> str:
    """Revision token derived purely from body bytes."""
    return "cr-" + content_hash(content)[len("sha256:"):][:16]


def resource_revision(*, resource_id: str, virtual_path: str,
                      content: bytes | str, metadata_hash: str = "") -> str:
    """Object-level CAS token: changes on content OR metadata OR path change.

    Deterministic function of (id, path, content, metadata) so an unchanged
    object always maps to the same revision (supports NO_CHANGE detection).
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    h = hashlib.sha256()
    h.update(resource_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(virtual_path.encode("utf-8"))
    h.update(b"\x00")
    h.update(metadata_hash.encode("utf-8"))
    h.update(b"\x00")
    h.update(content)
    return "rev-" + h.hexdigest()[:16]


def request_hash(canonical_request: str) -> str:
    """Idempotency request hash for lost-response replay detection (§6.2)."""
    return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
