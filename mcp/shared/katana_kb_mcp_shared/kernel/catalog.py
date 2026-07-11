"""Resource identity catalog (design §5.3, §6.1, §6.2, INV-6).

Maps immutable ``resource_id`` ↔ mutable ``virtual_path`` for one data repo.
The catalog is **canonical Git state**: its JSON lives under the reserved ``.kb``
namespace (hidden from ordinary fs_* traffic) and is committed *atomically with
the content it describes* inside the same MutationBatch/transaction. It is never
written to disk outside a committed transaction, so a failed validation or
publish leaves zero catalog mutation visible (design §6.6). Minted ids never
repeat, even after delete (tombstone), preventing ABA.
"""
from __future__ import annotations

import json
import os

from . import identity

KB_DIR = ".kb"
CATALOG_REL = os.path.join(KB_DIR, "catalog.json")


class Catalog:
    def __init__(self, repo_root: str, *, id_prefix: str) -> None:
        self.repo_root = repo_root
        self.id_prefix = id_prefix
        self._path = os.path.join(repo_root, CATALOG_REL)
        self._data = self._load()
        self._dirty = False

    def _load(self) -> dict:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    d = json.load(f)
                    d.setdefault("by_id", {})
                    d.setdefault("tombstones", [])
                    return d
            except (OSError, ValueError):
                pass
        return {"by_id": {}, "tombstones": []}

    # ── transactional persistence contract ───────────────────────────
    @property
    def dirty(self) -> bool:
        return self._dirty

    def serialize(self) -> bytes:
        """Canonical bytes for .kb/catalog.json (committed with the batch)."""
        return json.dumps(self._data, sort_keys=True,
                          ensure_ascii=False).encode("utf-8")

    def mark_clean(self) -> None:
        """Called by the engine after the transaction durably commits."""
        self._dirty = False

    def reload(self) -> None:
        """Discard uncommitted in-memory changes (failed/aborted transaction)."""
        self._data = self._load()
        self._dirty = False

    # ── lookups ───────────────────────────────────────────────────────
    def path_of(self, resource_id: str) -> str | None:
        return self._data["by_id"].get(resource_id)

    def id_of(self, virtual_path: str) -> str | None:
        for rid, p in self._data["by_id"].items():
            if p == virtual_path:
                return rid
        return None

    def all_ids(self) -> set[str]:
        return set(self._data["by_id"]) | set(self._data["tombstones"])

    def entries(self) -> dict[str, str]:
        return dict(self._data["by_id"])

    # ── in-memory mutations (persisted only via committed transaction) ─
    def mint(self, virtual_path: str) -> str:
        rid = identity.mint_id(self.id_prefix, self.all_ids())
        self._data["by_id"][rid] = virtual_path
        self._dirty = True
        return rid

    def bind(self, resource_id: str, virtual_path: str) -> None:
        """Bind a caller-supplied id (e.g. a domain card id) to a path."""
        if self._data["by_id"].get(resource_id) != virtual_path:
            self._data["by_id"][resource_id] = virtual_path
            self._dirty = True

    def rebind(self, resource_id: str, virtual_path: str) -> None:
        if self._data["by_id"].get(resource_id) != virtual_path:
            self._data["by_id"][resource_id] = virtual_path
            self._dirty = True

    def tombstone(self, resource_id: str) -> None:
        if resource_id in self._data["by_id"]:
            self._data["by_id"].pop(resource_id, None)
            self._dirty = True
        if resource_id not in self._data["tombstones"]:
            self._data["tombstones"].append(resource_id)
            self._dirty = True

    def is_tombstoned(self, resource_id: str) -> bool:
        return resource_id in self._data["tombstones"]
