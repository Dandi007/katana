"""AuthN/Z: bearer token authentication, credential registry, scope enforcement.

Credentials store only token hashes (never plaintext). Token only passed via
Authorization: Bearer header, never in tool args, Git, logs, or config.

Design §7.1: credential binds principal, tenant, domains, scopes, expiry, status.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable

SCOPE_ALL = "*"

UNAUTHORIZED = 401
FORBIDDEN = 403
RATE_LIMITED = 429


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_bearer_token(headers: dict[str, str]) -> str | None:
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip()


@dataclass
class AuthPrincipal:
    principal_id: str
    tenant: str
    scopes: set[str]
    domains: set[str] = field(default_factory=set)


@dataclass
class AuthToken:
    token_hash: str
    principal: AuthPrincipal
    expires_at: float | None = None
    revoked: bool = False
    last_used_at: float | None = None


@dataclass
class CredentialEntry:
    token_hash: str
    principal_id: str
    tenant: str
    domains: set[str]
    scopes: set[str]
    expires_at: float | None = None
    revoked: bool = False
    last_used_at: float | None = None
    created_at: float = field(default_factory=time.time)
    rotated_at: float | None = None

    def to_token(self) -> AuthToken:
        return AuthToken(
            token_hash=self.token_hash,
            principal=AuthPrincipal(
                principal_id=self.principal_id,
                tenant=self.tenant,
                scopes=self.scopes,
                domains=self.domains,
            ),
            expires_at=self.expires_at,
            revoked=self.revoked,
            last_used_at=self.last_used_at,
        )


class CredentialRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, CredentialEntry] = {}

    def register(
        self,
        token: str,
        principal_id: str,
        tenant: str,
        *,
        domains: set[str] | None = None,
        scopes: set[str] | None = None,
        expires_at: float | None = None,
    ) -> CredentialEntry:
        entry = CredentialEntry(
            token_hash=hash_token(token),
            principal_id=principal_id,
            tenant=tenant,
            domains=domains or set(),
            scopes=scopes or set(),
            expires_at=expires_at,
        )
        self._entries[entry.token_hash] = entry
        return entry

    def authenticate(self, token: str) -> AuthToken | None:
        token_hash = hash_token(token)
        entry = self._entries.get(token_hash)
        if entry is None:
            return None
        if entry.revoked:
            return None
        if entry.expires_at is not None and time.time() > entry.expires_at:
            return None
        entry.last_used_at = time.time()
        return entry.to_token()

    def revoke(self, token: str) -> bool:
        token_hash = hash_token(token)
        entry = self._entries.get(token_hash)
        if entry is None:
            return False
        entry.revoked = True
        return True

    def rotate(self, old_token: str, new_token: str) -> CredentialEntry | None:
        old_hash = hash_token(old_token)
        entry = self._entries.get(old_hash)
        if entry is None or entry.revoked:
            return None
        if entry.expires_at is not None and time.time() > entry.expires_at:
            return None
        del self._entries[old_hash]
        new_entry = CredentialEntry(
            token_hash=hash_token(new_token),
            principal_id=entry.principal_id,
            tenant=entry.tenant,
            domains=entry.domains,
            scopes=entry.scopes,
            expires_at=entry.expires_at,
            created_at=entry.created_at,
            rotated_at=time.time(),
            last_used_at=entry.last_used_at,
        )
        self._entries[new_entry.token_hash] = new_entry
        return new_entry

    def get_entry(self, token: str) -> CredentialEntry | None:
        return self._entries.get(hash_token(token))

    def __len__(self) -> int:
        return len(self._entries)