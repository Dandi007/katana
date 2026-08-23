"""Env-driven remote-mode wiring shared by the domain servers.

Remote mode is opt-in: setting ``KATANA_REMOTE_CREDENTIALS`` to a credstore
file path switches a server's main() to the auth-wrapped app. Without it the
server keeps its historical unauthenticated local behavior.

``KATANA_REMOTE_AUDIT_LOG`` optionally points the audit trail at a JSONL file;
unset keeps the in-memory logger.
"""

from __future__ import annotations

import os

from katana_remote.audit import AuditLogger, FileAuditLogger
from katana_remote.credstore import ENV_CREDENTIALS, load_registry

ENV_AUDIT_LOG = "KATANA_REMOTE_AUDIT_LOG"


def remote_credentials_path() -> str | None:
    return os.environ.get(ENV_CREDENTIALS) or None


def registry_from_env():
    path = remote_credentials_path()
    if path is None:
        raise RuntimeError(f"{ENV_CREDENTIALS} is not set")
    return load_registry(path)


def audit_logger_from_env() -> AuditLogger:
    path = os.environ.get(ENV_AUDIT_LOG)
    return FileAuditLogger(path) if path else AuditLogger()
