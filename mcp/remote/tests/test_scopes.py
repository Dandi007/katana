"""Tests for scope enforcement."""
from katana_remote.scopes import (
    SCOPE_READ,
    SCOPE_QUERY,
    SCOPE_MUTATE,
    SCOPE_COMMAND,
    SCOPE_OPERATE,
    SCOPE_AUDIT,
    ALL_SCOPES,
    requires_scope,
    scope_required_for_operation,
)


def test_read_scope_allows_read_ops():
    scopes = {SCOPE_READ}
    assert requires_scope(scopes, "fs_read")
    assert requires_scope(scopes, "fs_stat")
    assert requires_scope(scopes, "fs_list")
    assert requires_scope(scopes, "fs_resolve")
    assert requires_scope(scopes, "memory_index")
    assert requires_scope(scopes, "memory_get")


def test_read_scope_denies_mutate_ops():
    scopes = {SCOPE_READ}
    assert not requires_scope(scopes, "fs_create")
    assert not requires_scope(scopes, "fs_write")
    assert not requires_scope(scopes, "memory_create")
    assert not requires_scope(scopes, "memory_update")


def test_read_scope_denies_query_ops():
    scopes = {SCOPE_READ}
    assert not requires_scope(scopes, "wiki_query")
    assert not requires_scope(scopes, "wiki_search")


def test_read_scope_denies_audit_ops():
    scopes = {SCOPE_READ}
    assert not requires_scope(scopes, "audit_query")
    assert not requires_scope(scopes, "audit_export")


def test_mutate_scope_allows_mutate_ops():
    scopes = {SCOPE_MUTATE, SCOPE_READ}
    assert requires_scope(scopes, "fs_create")
    assert requires_scope(scopes, "fs_write")
    assert requires_scope(scopes, "fs_delete")
    assert requires_scope(scopes, "fs_batch")
    assert requires_scope(scopes, "memory_create")
    assert requires_scope(scopes, "memory_update")
    assert requires_scope(scopes, "memory_delete")
    assert requires_scope(scopes, "memory_edit")


def test_query_scope_allows_query_ops():
    scopes = {SCOPE_QUERY}
    assert requires_scope(scopes, "wiki_query")
    assert requires_scope(scopes, "wiki_search")
    assert requires_scope(scopes, "wf_search")


def test_audit_scope_allows_audit_ops():
    scopes = {SCOPE_AUDIT}
    assert requires_scope(scopes, "audit_query")
    assert requires_scope(scopes, "audit_export")


def test_operate_scope_allows_operate_ops():
    scopes = {SCOPE_OPERATE}
    assert requires_scope(scopes, "read_ready")
    assert requires_scope(scopes, "write_ready")


def test_empty_scope_denies_everything():
    assert not requires_scope(set(), "fs_read")
    assert not requires_scope(set(), "fs_create")
    assert not requires_scope(set(), "wiki_query")


def test_unknown_operation_returns_none():
    assert scope_required_for_operation("nonexistent_op") is None


def test_unknown_operation_requires_no_scope():
    assert not requires_scope({SCOPE_READ}, "nonexistent_op")
    assert not requires_scope({"read", "mutate", "query", "operate", "audit"}, "nonexistent_op")


def test_fs_capabilities_requires_read_scope():
    assert scope_required_for_operation("fs_capabilities") == SCOPE_READ
    assert requires_scope({SCOPE_READ}, "fs_capabilities")
    assert not requires_scope({"mutate"}, "fs_capabilities")


def test_fs_glob_requires_query_scope():
    assert not requires_scope({SCOPE_READ}, "fs_glob")
    assert requires_scope({SCOPE_QUERY}, "fs_glob")


def test_all_operations_covered():
    for op in [
        "fs_resolve", "fs_stat", "fs_list", "fs_read", "fs_read_bytes",
        "memory_index", "memory_get", "memory_read",
        "wiki_list_docs", "wiki_lint_mechanical",
        "wf_list", "fs_capabilities",
        "initialize", "tools/list",
    ]:
        scope = scope_required_for_operation(op)
        assert scope == SCOPE_READ, f"{op} should require read, got {scope}"

    for op in [
        "fs_glob", "wiki_query", "wiki_search", "wf_search",
    ]:
        scope = scope_required_for_operation(op)
        assert scope == SCOPE_QUERY, f"{op} should require query, got {scope}"

    for op in [
        "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename", "fs_delete", "fs_batch",
        "memory_create", "memory_update", "memory_delete", "memory_edit",
        "wiki_ingest_plan", "wiki_ingest_apply",
        "wf_create", "wf_save", "wf_resume", "wf_append_progress", "wf_reindex",
    ]:
        scope = scope_required_for_operation(op)
        assert scope == SCOPE_MUTATE, f"{op} should require mutate, got {scope}"

    for op in ["read_ready", "write_ready"]:
        scope = scope_required_for_operation(op)
        assert scope == SCOPE_OPERATE, f"{op} should require operate, got {scope}"

    for op in ["audit_query", "audit_export"]:
        scope = scope_required_for_operation(op)
        assert scope == SCOPE_AUDIT, f"{op} should require audit, got {scope}"
