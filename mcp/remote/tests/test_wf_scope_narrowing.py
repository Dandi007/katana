"""Tests for work-folder scope narrowing (H7): deny-by-default write-side gate.

Maps to spec §3 test cases 1-9.
"""
from __future__ import annotations

import pytest

from katana_work_folder_mcp.scope_guard import (
    GOAL_WORKER_ALLOWED_OPS,
    ScopeAuditEntry,
    check_tool,
    clear_audit_log,
    get_audit_log,
    set_enforcement,
    is_enforcement_enabled,
    set_deny_by_default,
    is_deny_by_default,
)


def _allowed_set() -> list[str]:
    return sorted(GOAL_WORKER_ALLOWED_OPS)


@pytest.fixture(autouse=True)
def _reset_guard():
    set_enforcement(False)
    set_deny_by_default(True)
    clear_audit_log()
    yield
    set_enforcement(False)
    set_deny_by_default(True)
    clear_audit_log()


# ── Test 1: allowed set tools pass through ──────────────────────────────

def test_allowed_tool_passes_with_enforcement_on():
    set_enforcement(True)
    result = check_tool("wf_append_progress", folder_id="wf-abc123")
    assert result is None


def test_allowed_tool_passes_with_enforcement_off():
    set_enforcement(False)
    result = check_tool("fs_edit", folder_id="wf-abc123")
    assert result is None


# ── Test 2: denied set tools → error with tool name and allowed set ─────

def test_denied_tool_rejected_with_enforcement_on():
    set_enforcement(True)
    result = check_tool("wf_reindex")
    assert result is not None
    assert result["ok"] is False
    assert result["code"] == "SCOPE_DENIED"
    assert result["tool"] == "wf_reindex"
    assert result["allowed_set"] == _allowed_set()


def test_fs_delete_rejected_with_enforcement_on():
    set_enforcement(True)
    result = check_tool("fs_delete", folder_id="wf-abc123")
    assert result is not None
    assert result["code"] == "SCOPE_DENIED"
    assert result["tool"] == "fs_delete"
    assert result["folder_id"] == "wf-abc123"


# ── Test 3: deny-by-default — unknown tool → denied ──────────────────────

def test_unknown_tool_denied_by_default():
    set_enforcement(True)
    result = check_tool("wf_unknown_new_tool", folder_id="wf-abc123")
    assert result is not None
    assert result["code"] == "SCOPE_DENIED"
    assert result["tool"] == "wf_unknown_new_tool"


# ── Test 4: gray switch off → all pass through but audit ────────────────

def test_off_mode_passes_denied_tool_but_audits():
    set_enforcement(False)
    clear_audit_log()
    result = check_tool("wf_reindex", folder_id="wf-abc123")
    assert result is None
    entries = get_audit_log()
    assert len(entries) == 1
    assert entries[0].tool == "wf_reindex"
    assert entries[0].decision == "would_deny"
    assert entries[0].enforcement_enabled is False


def test_off_mode_passes_allowed_tool_and_audits():
    set_enforcement(False)
    clear_audit_log()
    result = check_tool("wf_append_progress", folder_id="wf-abc123")
    assert result is None
    entries = get_audit_log()
    assert len(entries) == 1
    assert entries[0].decision == "allow"


# ── Test 5: gray switch on → R1 filtering ───────────────────────────────

def test_on_mode_denies_wf_create():
    set_enforcement(True)
    result = check_tool("wf_create")
    assert result is not None
    assert result["code"] == "SCOPE_DENIED"


def test_on_mode_denies_fs_copy():
    set_enforcement(True)
    result = check_tool("fs_copy", folder_id="wf-abc123")
    assert result is not None
    assert result["code"] == "SCOPE_DENIED"


def test_on_mode_denies_fs_rename():
    set_enforcement(True)
    result = check_tool("fs_rename", folder_id="wf-abc123")
    assert result is not None
    assert result["code"] == "SCOPE_DENIED"


def test_on_mode_denies_fs_batch():
    set_enforcement(True)
    result = check_tool("fs_batch")
    assert result is not None
    assert result["code"] == "SCOPE_DENIED"


# ── Test 6: three required tools still pass through when on ──────────────

def test_on_mode_allows_wf_append_progress():
    set_enforcement(True)
    assert check_tool("wf_append_progress", folder_id="wf-abc123") is None


def test_on_mode_allows_wf_resume():
    set_enforcement(True)
    assert check_tool("wf_resume", folder_id="wf-abc123") is None


def test_on_mode_allows_fs_create():
    set_enforcement(True)
    assert check_tool("fs_create", folder_id="wf-abc123") is None


def test_on_mode_allows_fs_write():
    set_enforcement(True)
    assert check_tool("fs_write", folder_id="wf-abc123") is None


def test_on_mode_allows_fs_edit():
    set_enforcement(True)
    assert check_tool("fs_edit", folder_id="wf-abc123") is None


# ── Test 7: wf_save still passes through when on ─────────────────────────

def test_on_mode_allows_wf_save():
    set_enforcement(True)
    assert check_tool("wf_save", folder_id="wf-abc123") is None


# ── Test 8: mutation — allow-by-default → unknown tool passes ────────────

def test_mutation_allow_by_default_reverses_deny_behavior():
    import katana_work_folder_mcp.scope_guard as sg

    original = sg.is_deny_by_default()
    try:
        sg.set_deny_by_default(False)
        set_enforcement(True)
        result = check_tool("wf_unknown_new_tool")
        assert result is None
    finally:
        sg.set_deny_by_default(original)


# ── Test 9: mutation — empty allowed set → allowed tools fail ────────────

def test_mutation_empty_allowed_set_breaks_required_tools():
    import katana_work_folder_mcp.scope_guard as sg

    original_ops = sg.GOAL_WORKER_ALLOWED_OPS
    try:
        sg.GOAL_WORKER_ALLOWED_OPS = frozenset()
        set_enforcement(True)
        result = check_tool("wf_append_progress", folder_id="wf-abc123")
        assert result is not None
        assert result["code"] == "SCOPE_DENIED"

        result = check_tool("fs_edit", folder_id="wf-abc123")
        assert result is not None

        result = check_tool("wf_save", folder_id="wf-abc123")
        assert result is not None
    finally:
        sg.GOAL_WORKER_ALLOWED_OPS = original_ops


# ── Audit entry shape ────────────────────────────────────────────────────

def test_audit_entry_contains_required_fields():
    set_enforcement(True)
    check_tool("wf_reindex", folder_id="wf-abc123")
    entries = get_audit_log()
    entry = entries[0]
    assert entry.principal == "goal-worker"
    assert entry.tool == "wf_reindex"
    assert entry.folder_id == "wf-abc123"
    assert entry.decision == "deny"
    assert entry.allowed_set == _allowed_set()
    assert entry.enforcement_enabled is True


def test_audit_entry_off_mode_contains_required_fields():
    set_enforcement(False)
    check_tool("wf_create", folder_id="wf-abc123")
    entries = get_audit_log()
    entry = entries[0]
    assert entry.principal == "goal-worker"
    assert entry.tool == "wf_create"
    assert entry.folder_id == "wf-abc123"
    assert entry.decision == "would_deny"
    assert entry.allowed_set == _allowed_set()
    assert entry.enforcement_enabled is False


# ── Default state ────────────────────────────────────────────────────────

def test_default_enforcement_is_off():
    set_enforcement(False)
    assert is_enforcement_enabled() is False


def test_set_enforcement_toggles():
    set_enforcement(True)
    assert is_enforcement_enabled() is True
    set_enforcement(False)
    assert is_enforcement_enabled() is False


def test_default_deny_by_default_is_true():
    set_deny_by_default(True)
    assert is_deny_by_default() is True


def test_set_deny_by_default_toggles():
    set_deny_by_default(False)
    assert is_deny_by_default() is False
    set_deny_by_default(True)
    assert is_deny_by_default() is True