"""Resource identity catalog (design §5.3, §6.1, INV-6).

Maps immutable ``resource_id`` ↔ mutable ``virtual_path`` for one data repo.
The catalog is a canonical artifact conceptually, but is kept rebuildable from
committed manifests; here it is persisted under the reserved ``.kb`` namespace
(hidden from ordinary fs_* traffic) and minted ids never repeat, even after
delete (tombstone), preventing ABA.
"""
from __future__ import annotations

import json
import os

from . import identity

_KB_DIR = ".kb"
_CATALOG_FILE = os.path.join(_KB_DIR, "catalog.json")


class Catalog:
    def __init__(self, repo_root: str, *, id_prefix: str) -> None:
        self.repo_root = repo_root
        self.id_prefix = id_prefix
        self._path = os.path.join(repo_root, _CATALOG_FILE)
        self._data = self._load()

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

    def _save(self) -> None:
        os.makedirs(os.path.join(self.repo_root, _KB_DIR), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, sort_keys=True, ensure_ascii=False)

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

    # ── mutations (persisted immediately; rebuildable from manifests) ─
    def mint(self, virtual_path: str) -> str:
        rid = identity.mint_id(self.id_prefix, self.all_ids())
        self._data["by_id"][rid] = virtual_path
        self._save()
        return rid

    def bind(self, resource_id: str, virtual_path: str) -> None:
        self._data["by_id"][resource_id] = virtual_path
        self._save()

    def rebind(self, resource_id: str, virtual_path: str) -> None:
        self._data["by_id"][resource_id] = virtual_path
        self._save()

    def tombstone(self, resource_id: str) -> None:
        self._data["by_id"].pop(resource_id, None)
        if resource_id not in self._data["tombstones"]:
            self._data["tombstones"].append(resource_id)
        self._save()

    def is_tombstoned(self, resource_id: str) -> bool:
        return resource_id in self._data["tombstones"]
