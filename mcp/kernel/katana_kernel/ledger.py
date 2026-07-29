"""ResourceIdLedger: tombstone-persisted id ledger with no-reuse guarantee."""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path


class LedgerError(RuntimeError):
    """Resource ID governance state is unreadable or structurally invalid."""


class ResourceIdLedger:
    def __init__(self, path: str, prefix: str = "m-"):
        self._path = Path(path)
        self._prefix = prefix
        self._tombstones: set[str] = set()
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise LedgerError(
                f"cannot read resource ID ledger: {self._path}"
            ) from exc
        if not isinstance(data, dict):
            raise LedgerError("resource ID ledger root must be a JSON object")
        tombstones = data.get("tombstones", [])
        if not isinstance(tombstones, list):
            raise LedgerError("resource ID ledger tombstones must be a list")
        resource_id_re = re.compile(
            rf"^{re.escape(self._prefix)}[0-9a-f]{{6}}$"
        )
        for resource_id in tombstones:
            if (
                not isinstance(resource_id, str)
                or not resource_id_re.fullmatch(resource_id)
            ):
                raise LedgerError(
                    "resource ID ledger contains an invalid tombstone"
                )
        self._tombstones = set(tombstones)

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                {"tombstones": sorted(self._tombstones)},
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        temporary = self._path.with_name(
            f".{self._path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def tombstone(self, resource_id: str):
        self._tombstones.add(resource_id)
        self._save()

    def rollback_tombstone(self, resource_id: str):
        self._tombstones.discard(resource_id)
        self._save()

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
