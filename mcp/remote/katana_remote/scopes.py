"""AuthZ scopes: minimal scope set with explicit deny-by-default.

Design §7.1:
- read:     resolve/stat/list/read
- query:    search/domain query
- mutate:   governed VFS mutation
- command:  domain command
- operate:  readiness/sync/backup status
- audit:    audit query/export

Unspecified capabilities default deny.
"""

from __future__ import annotations

SCOPE_READ = "read"
SCOPE_QUERY = "query"
SCOPE_MUTATE = "mutate"
SCOPE_COMMAND = "command"
SCOPE_OPERATE = "operate"
SCOPE_AUDIT = "audit"
SCOPE_ALL = "*"

ALL_SCOPES = {SCOPE_READ, SCOPE_QUERY, SCOPE_MUTATE, SCOPE_COMMAND, SCOPE_OPERATE, SCOPE_AUDIT}

_READ_OPS = {
    "fs_resolve", "fs_stat", "fs_list", "fs_read", "fs_read_bytes",
    "memory_index", "memory_get", "memory_read",
    "wiki_list_docs", "wiki_lint_mechanical",
    "wf_list",
    "fs_capabilities",
    "initialize", "tools/list",
    # initialize, tools/list mapped to read scope is deliberate:
    # read-only tokens must be able to bootstrap an MCP session
    # (discover tools, establish session) before performing any
    # scoped operations.  These are metadata/negotiation, not VFS content.
}

_QUERY_OPS = {
    "fs_glob",
    "wiki_query", "wiki_search",
    "wf_search",
}

_MUTATE_OPS = {
    "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename", "fs_delete", "fs_batch",
    "memory_create", "memory_update", "memory_delete", "memory_edit",
    "wiki_ingest_plan", "wiki_ingest_apply",
    "wf_create", "wf_save", "wf_resume", "wf_append_progress", "wf_reindex",
}

_COMMAND_OPS: set[str] = set()

_OPERATE_OPS = {
    "read_ready", "write_ready",
}

_AUDIT_OPS = {
    "audit_query", "audit_export",
}

_OP_SCOPE_MAP: dict[str, str] = {}
for _op in _READ_OPS:
    _OP_SCOPE_MAP[_op] = SCOPE_READ
for _op in _QUERY_OPS:
    _OP_SCOPE_MAP[_op] = SCOPE_QUERY
for _op in _MUTATE_OPS:
    _OP_SCOPE_MAP[_op] = SCOPE_MUTATE
for _op in _COMMAND_OPS:
    _OP_SCOPE_MAP[_op] = SCOPE_COMMAND
for _op in _OPERATE_OPS:
    _OP_SCOPE_MAP[_op] = SCOPE_OPERATE
for _op in _AUDIT_OPS:
    _OP_SCOPE_MAP[_op] = SCOPE_AUDIT


def scope_required_for_operation(operation: str) -> str | None:
    return _OP_SCOPE_MAP.get(operation)


def requires_scope(principal_scopes: set[str], operation: str) -> bool:
    required = scope_required_for_operation(operation)
    if required is None:
        return False
    if SCOPE_ALL in principal_scopes:
        return True
    if required == SCOPE_READ and SCOPE_READ in principal_scopes:
        return True
    if required == SCOPE_QUERY:
        return SCOPE_QUERY in principal_scopes
    if required == SCOPE_MUTATE:
        return SCOPE_MUTATE in principal_scopes
    if required == SCOPE_COMMAND:
        return SCOPE_COMMAND in principal_scopes
    if required == SCOPE_OPERATE:
        return SCOPE_OPERATE in principal_scopes
    if required == SCOPE_AUDIT:
        return SCOPE_AUDIT in principal_scopes
    return required in principal_scopes
