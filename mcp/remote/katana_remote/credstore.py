"""Credential persistence: hashed credential entries in a JSON file.

File format (version 1)::

    {
      "version": 1,
      "credentials": [
        {"token_hash": "sha256:...", "principal_id": "...", "tenant": "...",
         "domains": [...], "scopes": [...], "expires_at": null,
         "revoked": false, "created_at": ..., "rotated_at": null,
         "last_used_at": null},
        ...
      ]
    }

Only token hashes are stored — plaintext tokens exist only in the mint
command's stdout and in client configs. A missing file loads as an empty
registry (fail-closed: every request 401s until credentials are minted).
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

from katana_remote.auth import CredentialEntry, CredentialRegistry

ENV_CREDENTIALS = "KATANA_REMOTE_CREDENTIALS"
TOKEN_PREFIX = "ktn_"
FILE_VERSION = 1

_ENTRY_FIELDS = (
    "token_hash", "principal_id", "tenant", "domains", "scopes",
    "expires_at", "revoked", "created_at", "rotated_at", "last_used_at",
)


def generate_token() -> str:
    """Mint a fresh plaintext token. Never persisted — hash it immediately."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _entry_to_dict(entry: CredentialEntry) -> dict:
    return {
        "token_hash": entry.token_hash,
        "principal_id": entry.principal_id,
        "tenant": entry.tenant,
        "domains": sorted(entry.domains),
        "scopes": sorted(entry.scopes),
        "expires_at": entry.expires_at,
        "revoked": entry.revoked,
        "created_at": entry.created_at,
        "rotated_at": entry.rotated_at,
        "last_used_at": entry.last_used_at,
    }


def _entry_from_dict(d: dict) -> CredentialEntry:
    unknown = set(d) - set(_ENTRY_FIELDS)
    if unknown:
        raise ValueError(f"unknown credential fields: {sorted(unknown)}")
    return CredentialEntry(
        token_hash=d["token_hash"],
        principal_id=d["principal_id"],
        tenant=d["tenant"],
        domains=set(d.get("domains", [])),
        scopes=set(d.get("scopes", [])),
        expires_at=d.get("expires_at"),
        revoked=bool(d.get("revoked", False)),
        created_at=d.get("created_at", 0.0),
        rotated_at=d.get("rotated_at"),
        last_used_at=d.get("last_used_at"),
    )


def load_entries(path: str | os.PathLike) -> list[CredentialEntry]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    version = data.get("version")
    if version != FILE_VERSION:
        raise ValueError(f"unsupported credstore version {version!r} in {p}")
    return [_entry_from_dict(d) for d in data.get("credentials", [])]


def save_entries(path: str | os.PathLike, entries: list[CredentialEntry]) -> None:
    """Atomic write (tmp + rename), file mode 0600."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"version": FILE_VERSION, "credentials": [_entry_to_dict(e) for e in entries]},
        indent=2, ensure_ascii=False,
    )
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    os.replace(tmp, p)


def load_registry(path: str | os.PathLike) -> CredentialRegistry:
    registry = CredentialRegistry()
    for entry in load_entries(path):
        registry.add_entry(entry)
    return registry
