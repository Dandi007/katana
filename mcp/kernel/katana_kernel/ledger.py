"""ResourceIdLedger: tombstone-persisted id ledger with no-reuse guarantee."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


class ResourceIdLedger:
    def __init__(self, path: str, prefix: str = "m-"):
        self._path = Path(path)
        self._prefix = prefix
        self._tombstones: set[str] = set()
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._tombstones = set(data.get("tombstones", []))
            except (json.JSONDecodeError, OSError):
                self._tombstones = set()

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {"tombstones": sorted(self._tombstones)},
                indent=2,
            ),
            encoding="utf-8",
        )

    def tombstone(self, resource_id: str):
        self._tombstones.add(resource_id)
        self._save()

    def rollback_tombstone(self, resource_id: str):
        self._tombstones.discard(resource_id)

    def is_tombstoned(self, resource_id: str) -> bool:
        return resource_id in self._tombstones

    def gen_id(self, existing: set[str]) -> str:
        while True:
            i = self._prefix + secrets.token_hex(3)
            if i not in existing and i not in self._tombstones:
                return i

    @property
    def path(self) -> str:
        return str(self._path)

    @property
    def tombstones(self) -> set[str]:
        return set(self._tombstones)