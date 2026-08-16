"""Tests for work-folder scope narrowing (H7): deny-by-default write-side gate.

Maps to spec §3 test cases 1-9.
"""
from __future__ import annotations

import importlib
import json
import subprocess

import pytest
from starlette.testclient import TestClient

from katana_work_folder_mcp.scope_guard import (
    GOAL_WORKER_ALLOWED_OPS,
    MAX_AUDIT_LOG_SIZE,
    ScopeAuditEntry,
    check_tool,
    clear_audit_log,
    get_audit_log,
    is_deny_by_default,
    is_enforcement_enabled,
    set_deny_by_default,
    set_enforcement,
)
from katana_work_folder_mcp import server as wf_server
from katana_work_folder_mcp.reindex import render_index


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


# ── Default state (verified via importlib.reload to bypass fixture) ──────

def test_default_enforcement_is_off():
    import katana_work_folder_mcp.scope_guard as sg

    importlib.reload(sg)
    try:
        assert sg.is_enforcement_enabled() is False
    finally:
        sg.set_enforcement(False)
        sg.set_deny_by_default(True)
        sg.clear_audit_log()


def test_set_enforcement_toggles():
    set_enforcement(True)
    assert is_enforcement_enabled() is True
    set_enforcement(False)
    assert is_enforcement_enabled() is False


def test_default_deny_by_default_is_true():
    import katana_work_folder_mcp.scope_guard as sg

    importlib.reload(sg)
    try:
        assert sg.is_deny_by_default() is True
    finally:
        sg.set_enforcement(False)
        sg.set_deny_by_default(True)
        sg.clear_audit_log()


def test_set_deny_by_default_toggles():
    set_deny_by_default(False)
    assert is_deny_by_default() is False
    set_deny_by_default(True)
    assert is_deny_by_default() is True


# ── Audit log size cap ───────────────────────────────────────────────────

def test_audit_log_capped_at_max_size():
    clear_audit_log()
    for i in range(MAX_AUDIT_LOG_SIZE + 100):
        check_tool("wf_unknown_tool_{}".format(i), folder_id="wf-abc123")
    entries = get_audit_log()
    assert len(entries) <= MAX_AUDIT_LOG_SIZE
    assert len(entries) > MAX_AUDIT_LOG_SIZE // 2


# ── Server-side wiring: guard is exercised through server tool entry points ──

_MCP_ACCEPT = {"Accept": "application/json, text/event-stream"}


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(
        "/.katana/runtime/\n",
        encoding="utf-8",
    )
    (tmp_path / "INDEX.md").write_text(render_index([]), encoding="utf-8")
    controls = tmp_path / ".katana"
    controls.mkdir()
    (controls / "flat-layout.json").write_text(
        '{"layout":"flat-id-v1","schema_version":1}\n',
        encoding="utf-8",
    )
    (controls / "tombstones.json").write_text(
        '{"tombstones":[]}\n',
        encoding="utf-8",
    )
    (controls / "legacy-manifest-inventory.json").write_text(
        '{"manifests":[],"schema_version":1}\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "flat fixture"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return str(tmp_path)


def _mcp_session_no_auth(client):
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
            "id": 1,
        },
        headers=_MCP_ACCEPT,
    )
    assert r.status_code == 200, f"Session init failed: {r.status_code} {r.text[:200]}"
    session_id = r.headers.get("mcp-session-id")
    assert session_id, f"No session ID: {dict(r.headers)}"
    return session_id


def _mcp_call_no_auth(client, session_id, tool_name, arguments=None):
    headers = {
        **_MCP_ACCEPT,
        "mcp-session-id": session_id,
    }
    params = {"name": tool_name}
    if arguments is not None:
        params["arguments"] = arguments
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "tools/call", "params": params, "id": 2},
        headers=headers,
    )
    return r


def _tool_result(response):
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        envelope = json.loads(line[6:])
        result = envelope.get("result", {})
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                return json.loads(item["text"])
    envelope = response.json()
    result = envelope.get("result", {})
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            return json.loads(item["text"])
    raise AssertionError(f"tool response has no JSON text content: {response.text}")


@pytest.fixture
def _server_setup(tmp_path):
    wf_root = _init_repo(tmp_path)
    wf_server.configure(wf_root)
    app = wf_server.mcp.http_app()
    yield app, tmp_path
    wf_server._repo_root = None
    wf_server._kernel = None
    wf_server._store = None
    wf_server._fs_tools = None


def test_server_layer_blocks_wf_create_with_enforcement_on(_server_setup):
    app, _tmp_path = _server_setup
    set_enforcement(True)
    with TestClient(app) as client:
        session_id = _mcp_session_no_auth(client)
        r = _mcp_call_no_auth(client, session_id, "wf_create", {"topic": "test"})
        result = _tool_result(r)
        assert result["ok"] is False
        assert result["code"] == "SCOPE_DENIED"
        assert result["tool"] == "wf_create"
        assert result["allowed_set"] == _allowed_set()


def test_server_layer_blocks_wf_reindex_with_enforcement_on(_server_setup):
    app, _tmp_path = _server_setup
    set_enforcement(True)
    with TestClient(app) as client:
        session_id = _mcp_session_no_auth(client)
        r = _mcp_call_no_auth(client, session_id, "wf_reindex")
        result = _tool_result(r)
        assert result["ok"] is False
        assert result["code"] == "SCOPE_DENIED"
        assert result["tool"] == "wf_reindex"


def test_server_layer_blocks_fs_delete_with_enforcement_on(_server_setup):
    app, _tmp_path = _server_setup
    set_enforcement(True)
    with TestClient(app) as client:
        session_id = _mcp_session_no_auth(client)
        r = _mcp_call_no_auth(
            client, session_id, "fs_delete",
            {"folder_id": "wf-abc123", "filename": "test.txt"},
        )
        result = _tool_result(r)
        assert result["ok"] is False
        assert result["code"] == "SCOPE_DENIED"
        assert result["tool"] == "fs_delete"


def test_server_layer_blocks_fs_copy_with_enforcement_on(_server_setup):
    app, _tmp_path = _server_setup
    set_enforcement(True)
    with TestClient(app) as client:
        session_id = _mcp_session_no_auth(client)
        r = _mcp_call_no_auth(
            client, session_id, "fs_copy",
            {
                "source_folder_id": "wf-abc",
                "source_filename": "a.txt",
                "dest_folder_id": "wf-def",
                "dest_filename": "b.txt",
            },
        )
        result = _tool_result(r)
        assert result["ok"] is False
        assert result["code"] == "SCOPE_DENIED"


def test_server_layer_blocks_fs_rename_with_enforcement_on(_server_setup):
    app, _tmp_path = _server_setup
    set_enforcement(True)
    with TestClient(app) as client:
        session_id = _mcp_session_no_auth(client)
        r = _mcp_call_no_auth(
            client, session_id, "fs_rename",
            {
                "source_folder_id": "wf-abc",
                "source_filename": "a.txt",
                "dest_folder_id": "wf-def",
                "dest_filename": "b.txt",
            },
        )
        result = _tool_result(r)
        assert result["ok"] is False
        assert result["code"] == "SCOPE_DENIED"


def test_server_layer_blocks_fs_batch_with_enforcement_on(_server_setup):
    app, _tmp_path = _server_setup
    set_enforcement(True)
    with TestClient(app) as client:
        session_id = _mcp_session_no_auth(client)
        r = _mcp_call_no_auth(
            client, session_id, "fs_batch",
            {"operations": []},
        )
        result = _tool_result(r)
        assert result["ok"] is False
        assert result["code"] == "SCOPE_DENIED"


def test_server_layer_off_mode_passes_denied_tool(_server_setup):
    app, _tmp_path = _server_setup
    set_enforcement(False)
    clear_audit_log()
    with TestClient(app) as client:
        session_id = _mcp_session_no_auth(client)
        r = _mcp_call_no_auth(client, session_id, "wf_create", {"topic": "test"})
        result = _tool_result(r)
        assert result.get("code") != "SCOPE_DENIED"
    entries = get_audit_log()
    assert len(entries) == 1
    assert entries[0].tool == "wf_create"
    assert entries[0].decision == "would_deny"