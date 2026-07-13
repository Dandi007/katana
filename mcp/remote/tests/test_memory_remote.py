"""Contract tests for memory app with remote auth layer.

Tests that the memory app can be wrapped with remote auth and that
authenticated HTTP calls work correctly, including fs_* mutation with CAS.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from katana_remote import (
    CredentialRegistry,
    RateLimiter,
    RateLimitConfig,
    ReadinessService,
    AuditLogger,
    UNAUTHORIZED,
    FORBIDDEN,
    RATE_LIMITED,
)
from katana_remote.middleware import create_remote_app
from katana_memory_mcp import server as memory_server
from starlette.testclient import TestClient


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    d = tmp_path / "uther"
    d.mkdir()
    return str(tmp_path), str(d)


def _make_memory_app_with_auth(tmp_path, credential_registry, **kwargs):
    """Build a memory build_app with remote auth middleware wrapping it."""
    data_root, tdir = _init_repo(tmp_path)
    inner = memory_server.build_app(data_root)
    return create_remote_app(
        inner,
        credential_registry=credential_registry,
        domain="memory",
        **kwargs,
    ), data_root


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


_MCP_ACCEPT = {"Accept": "application/json, text/event-stream"}


def _mcp_session(client, token, tenant="uther"):
    """Create an MCP session and return the session ID."""
    headers = {**_auth_header(token), **_MCP_ACCEPT}
    r = client.post(
        f"/t/{tenant}/mcp",
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
        headers=headers,
    )
    assert r.status_code == 200, f"Session init failed: {r.status_code} {r.text[:200]}"
    session_id = r.headers.get("mcp-session-id")
    assert session_id, f"No session ID in response headers: {dict(r.headers)}"
    return session_id


def _mcp_call(client, token, session_id, tool_name, arguments=None, tenant="uther"):
    """Make an MCP tool call with session management."""
    headers = {
        **_auth_header(token),
        **_MCP_ACCEPT,
        "mcp-session-id": session_id,
    }
    params = {"name": tool_name}
    if arguments is not None:
        params["arguments"] = arguments
    r = client.post(
        f"/t/{tenant}/mcp",
        json={"jsonrpc": "2.0", "method": "tools/call", "params": params, "id": 2},
        headers=headers,
    )
    return r


def _parse_sse_json(response):
    """Parse JSON from SSE response body."""
    text = response.text
    for line in text.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(text)


class TestMemoryRemoteAuth:
    def test_livez_no_auth(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            r = client.get("/livez")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

    def test_unauthorized_no_token(self, tmp_path):
        creds = CredentialRegistry()
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            r = client.post("/t/uther/mcp", json={"method": "tools/call", "params": {"name": "memory_index"}})
            assert r.status_code == UNAUTHORIZED

    def test_authenticated_read_works(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            session_id = _mcp_session(client, "full-token")
            r = _mcp_call(client, "full-token", session_id, "memory_index")
            assert r.status_code == 200

    def test_read_only_cannot_mutate(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("read-only", "alice", "uther", scopes={"read"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            r = client.post("/t/uther/mcp",
                            json={"method": "tools/call", "params": {
                                "name": "memory_create",
                                "arguments": {"name": "test", "description": "test",
                                              "body": "## Fact\nx\n\n## How to Verify\ny"}}},
                            headers=_auth_header("read-only"))
            assert r.status_code == FORBIDDEN

    def test_tenant_mismatch_rejected(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "tenant-a", scopes={"read", "mutate"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            r = client.post("/t/uther/mcp",
                            json={"method": "tools/call", "params": {"name": "memory_index"}},
                            headers=_auth_header("full-token"))
            assert r.status_code == FORBIDDEN
            assert r.json()["code"] == "TENANT_MISMATCH"

    def test_audit_logs_requests(self, tmp_path):
        audit_logger = AuditLogger()
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds, audit_logger=audit_logger)
        with TestClient(app) as client:
            session_id = _mcp_session(client, "full-token")
            _mcp_call(client, "full-token", session_id, "memory_index")

            entries = audit_logger.entries()
            assert len(entries) > 0
            last_entry = entries[-1]
            assert last_entry.principal_id == "alice"
            assert last_entry.tenant == "uther"
            assert last_entry.result == "success"
            assert last_entry.client_identity != "unknown"

    def test_token_not_in_audit_or_error(self, tmp_path):
        audit_logger = AuditLogger()
        creds = CredentialRegistry()
        creds.register("secret-token-123", "alice", "uther", scopes={"read"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds, audit_logger=audit_logger)
        with TestClient(app) as client:
            r = client.post("/t/uther/mcp",
                            json={"method": "tools/call", "params": {
                                "name": "memory_create",
                                "arguments": {"name": "test", "description": "test"}}},
                            headers=_auth_header("secret-token-123"))
            assert r.status_code == FORBIDDEN
            body = r.json()
            assert "secret-token-123" not in json.dumps(body)

            for entry in audit_logger.entries():
                entry_str = json.dumps(entry.__dict__ if hasattr(entry, "__dict__") else str(entry))
                assert "secret-token-123" not in entry_str

    def test_rate_limit_exceeded(self, tmp_path):
        config = RateLimitConfig(requests_per_minute=4)
        limiter = RateLimiter(config)
        creds = CredentialRegistry()
        creds.register("heavy", "alice", "uther", scopes={"read"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds, rate_limiter=limiter)
        with TestClient(app) as client:
            session_id = _mcp_session(client, "heavy")
            for _ in range(3):
                r = _mcp_call(client, "heavy", session_id, "memory_index")
                assert r.status_code == 200

            r = _mcp_call(client, "heavy", session_id, "memory_index")
            assert r.status_code == RATE_LIMITED
            assert r.json()["code"] == "RATE_LIMITED"
            assert r.json()["retryable"] is True

    def test_authenticated_mutation_works(self, tmp_path):
        audit_logger = AuditLogger()
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds, audit_logger=audit_logger)
        with TestClient(app) as client:
            session_id = _mcp_session(client, "full-token")
            r = _mcp_call(client, "full-token", session_id, "memory_create", arguments={
                "name": "test-card",
                "description": "test desc",
                "body": "## Fact\nx\n\n## How to Verify\ny",
            })
            assert r.status_code == 200

            result = _parse_sse_json(r)
            assert "result" in result
            if "content" in result.get("result", {}):
                content = result["result"]["content"]
                if isinstance(content, list) and len(content) > 0:
                    text = content[0].get("text", "")
                    data = json.loads(text)
                    assert "id" in data

            entries = audit_logger.entries()
            assert len(entries) > 0
            mutation_entries = [e for e in entries if e.operation == "memory_create" and e.result == "success"]
            assert len(mutation_entries) > 0
            mut_entry = mutation_entries[-1]
            assert mut_entry.resulting_commit is not None, f"audit entry missing resulting_commit: {mut_entry}"
            assert mut_entry.client_identity != "unknown", f"audit entry missing client_identity: {mut_entry}"

    def test_fs_create_mutation_with_cas(self, tmp_path):
        """R1+R2: fs_* mutation over HTTP with CAS guard + stable error envelope."""
        audit_logger = AuditLogger()
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, data_root = _make_memory_app_with_auth(tmp_path, creds, audit_logger=audit_logger)

        with TestClient(app) as client:
            session_id = _mcp_session(client, "full-token")

            r0 = _mcp_call(client, "full-token", session_id, "memory_create", arguments={
                "name": "seed-card",
                "description": "seed card for CAS test",
                "body": "## Fact\nseed\n\n## How to Verify\nseed\n",
            })
            assert r0.status_code == 200, f"seed create failed: {r0.status_code} {r0.text[:500]}"

            base_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=data_root,
                check=True, capture_output=True, text=True,
            ).stdout.strip()

            r = _mcp_call(client, "full-token", session_id, "fs_create", arguments={
                "path": "uther/fs-test-card.md",
                "content": (
                    "---\n"
                    "name: fs-test-card\n"
                    "description: test fs_* mutation with CAS\n"
                    "---\n\n"
                    "## Fact\nTest CAS mutation.\n\n"
                    "## How to Verify\nRun the test.\n"
                ),
                "expected_base_sha": base_commit,
            })
            assert r.status_code == 200, f"fs_create with CAS failed: {r.status_code} {r.text[:500]}"

            result = _parse_sse_json(r)
            assert "result" in result
            inner = result.get("result", {})
            content_list = inner.get("content", [])
            assert len(content_list) > 0
            text = content_list[0].get("text", "")
            data = json.loads(text)
            new_commit = data.get("commit")
            assert new_commit is not None, f"no commit in fs_create response: {data}"
            assert new_commit != base_commit, f"commit should have changed: base={base_commit[:8]} new={new_commit[:8]}"
            assert "resource_id" in data

            entries = audit_logger.entries()
            fs_entries = [e for e in entries if e.operation == "fs_create" and e.result == "success"]
            assert len(fs_entries) > 0, f"no fs_create audit entry in: {[(e.operation, e.result) for e in entries]}"
            fs_entry = fs_entries[-1]
            assert fs_entry.principal_id == "alice"
            assert fs_entry.tenant == "uther"
            assert fs_entry.resulting_commit is not None
            assert fs_entry.client_identity != "unknown"

            r2 = _mcp_call(client, "full-token", session_id, "fs_create", arguments={
                "path": "uther/fs-test-card-2.md",
                "content": (
                    "---\n"
                    "name: fs-test-card-2\n"
                    "description: CAS mismatch test\n"
                    "---\n\n"
                    "## Fact\nCAS mismatch.\n\n"
                    "## How to Verify\nRun the test.\n"
                ),
                "expected_base_sha": base_commit,
            })
            assert r2.status_code == 200, f"CAS mismatch should return HTTP 200 with error envelope: {r2.status_code} {r2.text[:500]}"

            cas_result = _parse_sse_json(r2)
            assert "error" in cas_result or "result" in cas_result

            error_data = None
            if "error" in cas_result:
                error_data = cas_result["error"]
            elif "result" in cas_result:
                inner_r = cas_result["result"]
                content_items = inner_r.get("content", [])
                if content_items and isinstance(content_items, list):
                    inner_text = content_items[0].get("text", "")
                    try:
                        error_data = json.loads(inner_text)
                    except Exception:
                        error_data = inner_r

            assert error_data is not None, f"no error data in CAS mismatch response: {cas_result}"
            assert error_data.get("code") == "BASE_COMMIT_CONFLICT", \
                f"expected BASE_COMMIT_CONFLICT, got {error_data.get('code')}: {error_data}"
            assert error_data.get("retryable") is True, \
                f"CAS conflict should be retryable: {error_data}"
            assert "message" in error_data
            assert "full-token" not in json.dumps(error_data), \
                f"token leaked into error body: {error_data}"
            assert "full-token" not in r2.text, \
                f"token leaked into HTTP response body"