"""Work Folder scope guard: deny-by-default write-side narrowing for the goal worker seat.

Route (b): server-side equivalent gate.  Defaults to ``off`` (audit-only, no blocking).
When ``on``, only the tools in ``GOAL_WORKER_ALLOWED_OPS`` are permitted; everything
else is denied with a stable error containing the tool name and the allowed set.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

GOAL_WORKER_ALLOWED_OPS = frozenset({
    "wf_append_progress",
    "wf_resume",
    "fs_create",
    "fs_write",
    "fs_edit",
    "wf_save",
})

_scope_enforcement_enabled: bool = False

_scope_audit_log: list[ScopeAuditEntry] = []


@dataclass
class ScopeAuditEntry:
    timestamp: str
    principal: str
    tool: str
    folder_id: str | None
    decision: str
    allowed_set: list[str]
    enforcement_enabled: bool
    extra: dict[str, Any] = field(default_factory=dict)


def set_enforcement(enabled: bool) -> None:
    global _scope_enforcement_enabled
    _scope_enforcement_enabled = bool(enabled)


def is_enforcement_enabled() -> bool:
    return _scope_enforcement_enabled


def clear_audit_log() -> None:
    _scope_audit_log.clear()


def get_audit_log() -> list[ScopeAuditEntry]:
    return list(_scope_audit_log)


def _record_audit(
    tool: str,
    folder_id: str | None,
    decision: str,
    principal: str = "goal-worker",
) -> None:
    _scope_audit_log.append(
        ScopeAuditEntry(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            principal=principal,
            tool=tool,
            folder_id=folder_id,
            decision=decision,
            allowed_set=sorted(GOAL_WORKER_ALLOWED_OPS),
            enforcement_enabled=_scope_enforcement_enabled,
        )
    )


def check_tool(tool: str, folder_id: str | None = None) -> dict | None:
    """Check whether *tool* is permitted for the goal worker seat.

    Returns ``None`` when the tool is allowed (pass-through), or a stable error
    dict when the tool is denied.  Every call writes an audit entry regardless
    of the enforcement state.
    """
    allowed = tool in GOAL_WORKER_ALLOWED_OPS

    if _scope_enforcement_enabled:
        if allowed:
            _record_audit(tool, folder_id, "allow")
            return None
        else:
            _record_audit(tool, folder_id, "deny")
            return {
                "ok": False,
                "code": "SCOPE_DENIED",
                "message": (
                    f"tool '{tool}' is not permitted for the goal worker seat; "
                    f"allowed tools: {sorted(GOAL_WORKER_ALLOWED_OPS)}"
                ),
                "tool": tool,
                "allowed_set": sorted(GOAL_WORKER_ALLOWED_OPS),
                "folder_id": folder_id,
            }
    else:
        if allowed:
            _record_audit(tool, folder_id, "allow")
        else:
            _record_audit(tool, folder_id, "would_deny")
        return None