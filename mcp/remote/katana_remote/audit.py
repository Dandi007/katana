"""Audit logging: structured, redacted audit trail for authenticated requests.

Design §7.3:
Every authenticated request records: request/mutation ID, principal, tenant/domain,
scope, operation, resource IDs, base/resulting commit, policy version, result/error,
client identity, server time. Logs default redacted — no token, binary payload, or
full body in audit.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditEntry:
    request_id: str
    mutation_id: str | None
    principal_id: str
    tenant: str
    domain: str
    scopes: list[str]
    operation: str
    resource_ids: list[str]
    base_commit: str | None
    resulting_commit: str | None
    policy_version: str
    result: str
    error: str | None
    client_identity: str
    server_time: str
    extra: dict[str, Any] = field(default_factory=dict)


class AuditLogger:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def log(self, entry: AuditEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def query(self, principal: str | None = None,
              tenant: str | None = None,
              operation: str | None = None,
              limit: int = 100) -> list[AuditEntry]:
        result = self._entries
        if principal:
            result = [e for e in result if e.principal_id == principal]
        if tenant:
            result = [e for e in result if e.tenant == tenant]
        if operation:
            result = [e for e in result if e.operation == operation]
        return result[-limit:]

    def __len__(self) -> int:
        return len(self._entries)


def sanitize(data: dict[str, Any]) -> dict[str, Any]:
    REDACTED = "[REDACTED]"
    sensitive_keys = {"token", "authorization", "password", "secret", "api_key",
                       "bearer", "credential", "binary", "body", "payload", "content"}
    result = {}
    for k, v in data.items():
        if k.lower() in sensitive_keys:
            result[k] = REDACTED
        elif isinstance(v, (bytes, bytearray)):
            result[k] = REDACTED
        elif isinstance(v, dict):
            result[k] = sanitize(v)
        elif isinstance(v, (list, tuple)):
            result[k] = [
                sanitize(item) if isinstance(item, dict) else
                REDACTED if isinstance(item, (bytes, bytearray)) else item
                for item in v
            ]
        else:
            result[k] = v
    return result


def audit_log(
    logger: AuditLogger,
    principal_id: str,
    tenant: str,
    domain: str,
    scopes: list[str],
    operation: str,
    resource_ids: list[str] | None = None,
    base_commit: str | None = None,
    resulting_commit: str | None = None,
    result: str | None = None,
    error: str | None = None,
    client_identity: str = "unknown",
    mutation_id: str | None = None,
    policy_version: str = "1.0",
    **extra: Any,
) -> AuditEntry:
    entry = AuditEntry(
        request_id=str(uuid.uuid4()),
        mutation_id=mutation_id or str(uuid.uuid4()),
        principal_id=principal_id,
        tenant=tenant,
        domain=domain,
        scopes=scopes or [],
        operation=operation,
        resource_ids=resource_ids or [],
        base_commit=base_commit,
        resulting_commit=resulting_commit,
        policy_version=policy_version,
        result=result or ("success" if error is None else "error"),
        error=error,
        client_identity=client_identity,
        server_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        extra=sanitize(extra),
    )
    logger.log(entry)
    return entry