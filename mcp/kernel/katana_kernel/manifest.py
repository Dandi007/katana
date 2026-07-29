"""TransactionManifest: durable record of governed operations.

Manifests are written atomically: first to a staging file, then atomically
renamed to the final manifest path. If commit fails, staging files are cleaned
up, ensuring no dirty manifest files linger.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from collections.abc import Callable


class ManifestError(Exception):
    """Raised for manifest operation failures."""


class TransactionManifest:
    def __init__(self, manifests_dir: str):
        self._dir = Path(manifests_dir)
        self._staging_dir = self._dir / ".staging"

    def _ensure_dirs(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    def record(self, domain: str, op: str, result: dict, git_result: dict | None = None,
               changed_paths: list[str] | None = None,
               before_write: Callable[[str], None] | None = None) -> dict:
        self._ensure_dirs()
        ts = int(time.time() * 1_000_000)
        resource_id = result.get("id", "unknown")
        manifest_id = f"{domain}-{op}-{resource_id}-{ts}"
        staging_path = self._staging_dir / f"{manifest_id}.json"
        if before_write is not None:
            before_write(str(staging_path))

        record = {
            "manifest_id": manifest_id,
            "domain": domain,
            "op": op,
            "timestamp": ts,
            "result": {k: v for k, v in result.items() if k != "changed_paths"},
            "changed_paths": changed_paths or [],
            "git": git_result or {},
        }
        with staging_path.open("x", encoding="utf-8") as staging_file:
            staging_file.write(json.dumps(record, indent=2))
        return record

    def commit_manifests(self, manifest_ids: list[str] | None = None) -> dict:
        self._ensure_dirs()
        if manifest_ids is None:
            staged = sorted(self._staging_dir.glob("*.json"))
        else:
            staged = []
            for manifest_id in manifest_ids:
                name = (
                    manifest_id
                    if manifest_id.endswith(".json")
                    else f"{manifest_id}.json"
                )
                path = self._staging_dir / name
                if not path.is_file():
                    raise ManifestError(f"staged manifest not found: {name}")
                staged.append(path)
        moved = []
        for sp in staged:
            dest = self._dir / sp.name
            os.rename(str(sp), str(dest))
            moved.append(sp.name)
        return {"moved": len(moved), "manifests": moved}

    def rollback_staging(self):
        self._ensure_dirs()
        for sp in self._staging_dir.glob("*.json"):
            sp.unlink()

    def rollback_committed(self, manifest_ids: list[str]):
        self._ensure_dirs()
        for mid in manifest_ids:
            fname = mid if mid.endswith(".json") else f"{mid}.json"
            final_path = self._dir / fname
            staging_path = self._staging_dir / fname
            if final_path.exists():
                os.rename(str(final_path), str(staging_path))

    def list_manifests(self) -> list[dict]:
        self._ensure_dirs()
        manifests = []
        for mp in sorted(self._dir.glob("*.json")):
            try:
                manifests.append(json.loads(mp.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return manifests

    def get_manifest(self, manifest_id: str) -> dict | None:
        self._ensure_dirs()
        path = self._dir / f"{manifest_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @property
    def manifests_dir(self) -> str:
        return str(self._dir)

    @property
    def staging_dir(self) -> str:
        return str(self._staging_dir)
