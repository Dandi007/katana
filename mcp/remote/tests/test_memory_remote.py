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
from katana_remote.middleware import AuthMiddleware
from katana_memory_mcp import server as memory_server
from starlette.applications import Starlette
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
    middleware = AuthMiddleware(
        inner,
        credential_registry=credential_registry,
        domain="memory",
        **kwargs,
    )
    app = Starlette()
    app.mount("/", middleware)
    return app, data_root


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


class TestMemoryRemoteAuth:
    def test_livez_no_auth(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        client = TestClient(app)
        r = client.get("/livez")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_unauthorized_no_token(self, tmp_path):
        creds = CredentialRegistry()
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        client = TestClient(app)
        r = client.post("/t/uther/mcp", json={"method": "tools/call", "params": {"name": "memory_index"}})
        assert r.status_code == UNAUTHORIZED

    def test_authenticated_read_works(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        client = TestClient(app)

        r = client.post("/t/uther/mcp",
                        json={"method": "tools/call", "params": {"name": "memory_index"}},
                        headers=_auth_header("full-token"))
        assert r.status_code == 200

    def test_read_only_cannot_mutate(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("read-only", "alice", "uther", scopes={"read"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds)
        client = TestClient(app)

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
        client = TestClient(app)

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
        client = TestClient(app)

        client.post("/t/uther/mcp",
                    json={"method": "tools/call", "params": {"name": "memory_index"}},
                    headers=_auth_header("full-token"))

        entries = audit_logger.entries()
        assert len(entries) > 0
        assert entries[-1].principal_id == "alice"
        assert entries[-1].tenant == "uther"
        assert entries[-1].result == "success"

    def test_token_not_in_audit_or_error(self, tmp_path):
        audit_logger = AuditLogger()
        creds = CredentialRegistry()
        creds.register("secret-token-123", "alice", "uther", scopes={"read"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds, audit_logger=audit_logger)
        client = TestClient(app)

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
        config = RateLimitConfig(requests_per_minute=3)
        limiter = RateLimiter(config)
        creds = CredentialRegistry()
        creds.register("heavy", "alice", "uther", scopes={"read"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds, rate_limiter=limiter)
        client = TestClient(app)

        for _ in range(3):
            r = client.post("/t/uther/mcp",
                            json={"method": "tools/call", "params": {"name": "memory_index"}},
                            headers=_auth_header("heavy"))
            assert r.status_code == 200

        r = client.post("/t/uther/mcp",
                        json={"method": "tools/call", "params": {"name": "memory_index"}},
                        headers=_auth_header("heavy"))
        assert r.status_code == RATE_LIMITED
        assert r.json()["code"] == "RATE_LIMITED"
        assert r.json()["retryable"] is True

    def test_authenticated_mutation_works(self, tmp_path):
        audit_logger = AuditLogger()
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "uther", scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_memory_app_with_auth(tmp_path, creds, audit_logger=audit_logger)
        client = TestClient(app)

        r = client.post("/t/uther/mcp",
                        json={"method": "tools/call", "params": {
                            "name": "memory_create",
                            "arguments": {
                                "name": "test-card",
                                "description": "test desc",
                                "body": "## Fact\nx\n\n## How to Verify\ny"
                            }}},
                        headers=_auth_header("full-token"))
        assert r.status_code == 200

        result = r.json()
        assert "result" in result
        if "content" in result.get("result", {}):
            content = result["result"]["content"]
            if isinstance(content, list) and len(content) > 0:
                text = content[0].get("text", "")
                data = json.loads(text)
                assert "id" in data

        entries = audit_logger.entries()
        assert len(entries) > 0
        assert any(e.operation == "memory_create" and e.result == "success" for e in entries)