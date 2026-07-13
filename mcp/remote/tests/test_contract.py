"""Contract tests for remote HTTP auth layer covering all acceptance criteria.

Uses ASGI TestClient (httpx) to test authenticated HTTP without real network,
as required by spec §Acceptance.
"""

import json
import os
import subprocess
import time
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
from katana_remote.auth import extract_bearer_token, hash_token

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _make_simple_app():
    async def mcp_endpoint(request):
        body = {}
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                pass
        return JSONResponse({"jsonrpc": "2.0", "result": {"ok": True}, "id": body.get("id", 0)})

    return Starlette(routes=[
        Route("/mcp", mcp_endpoint, methods=["POST"]),
        Route("/t/{tenant}/mcp", mcp_endpoint, methods=["POST"]),
    ])


def _make_test_app(credential_registry=None, rate_limiter=None, readiness_service=None, audit_logger=None):
    from katana_remote.middleware import create_remote_app
    inner = _make_simple_app()
    creds = credential_registry or CredentialRegistry()
    return create_remote_app(
        inner,
        credential_registry=creds,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        domain="test",
    ), creds


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _register_token(creds, token="test-token", principal="alice", tenant="default",
                    scopes=None):
    if scopes is None:
        scopes = {"read", "mutate", "query", "operate", "audit"}
    creds.register(token, principal, tenant, scopes=scopes)


class TestNoTokenRejected:
    def test_no_token_returns_401(self):
        app, _ = _make_test_app()
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_read"}})
        assert r.status_code == UNAUTHORIZED
        assert r.json()["code"] == "UNAUTHORIZED"

    def test_bad_token_returns_401(self):
        app, creds = _make_test_app()
        _register_token(creds, token="real-token")
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("wrong-token"))
        assert r.status_code == UNAUTHORIZED

    def test_livez_no_auth_required(self):
        app, _ = _make_test_app()
        client = TestClient(app)
        r = client.get("/livez")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestExpiredAndRevoked:
    def test_expired_token_rejected(self):
        creds = CredentialRegistry()
        creds.register("old-token", "alice", "default",
                       scopes={"read"}, expires_at=time.time() - 3600)
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("old-token"))
        assert r.status_code == UNAUTHORIZED

    def test_revoked_token_rejected(self):
        creds = CredentialRegistry()
        creds.register("revoked-token", "alice", "default", scopes={"read"})
        creds.revoke("revoked-token")
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("revoked-token"))
        assert r.status_code == UNAUTHORIZED

    def test_unknown_token_rejected(self):
        app, creds = _make_test_app()
        _register_token(creds, token="known")
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("unknown-token"))
        assert r.status_code == UNAUTHORIZED


class TestScopeEnforcement:
    def test_read_only_token_cannot_mutate(self):
        creds = CredentialRegistry()
        creds.register("read-only", "alice", "default", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_create"}},
                        headers=_auth_header("read-only"))
        assert r.status_code == FORBIDDEN
        assert r.json()["code"] == "FORBIDDEN"

    def test_missing_query_scope_cannot_search(self):
        creds = CredentialRegistry()
        creds.register("read-only", "alice", "default", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "wiki_search"}},
                        headers=_auth_header("read-only"))
        assert r.status_code == FORBIDDEN

    def test_missing_audit_scope_cannot_query_audit(self):
        creds = CredentialRegistry()
        creds.register("reader", "alice", "default", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/audit_query", headers=_auth_header("reader"))
        assert r.status_code == FORBIDDEN

    def test_required_scope_allows_operation(self):
        creds = CredentialRegistry()
        creds.register("full-access", "alice", "default",
                       scopes={"read", "mutate", "query", "operate", "audit"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("full-access"))
        assert r.status_code == 200

    def test_unscoped_operation_default_deny(self):
        creds = CredentialRegistry()
        creds.register("minimal", "alice", "default", scopes=set())
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("minimal"))
        assert r.status_code == FORBIDDEN


class TestTenantConfinement:
    def test_url_tenant_mismatch_rejected(self):
        creds = CredentialRegistry()
        creds.register("my-token", "alice", "tenant-a", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/t/tenant-b/mcp",
                        json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("my-token"))
        assert r.status_code == FORBIDDEN
        assert r.json()["code"] == "TENANT_MISMATCH"

    def test_matching_tenant_allowed(self):
        creds = CredentialRegistry()
        creds.register("my-token", "alice", "tenant-a", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/t/tenant-a/mcp",
                        json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("my-token"))
        assert r.status_code == 200


class TestTokenRedaction:
    def test_token_not_in_error_body(self):
        creds = CredentialRegistry()
        creds.register("secret-token", "alice", "default", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)
        r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_create"}},
                        headers=_auth_header("secret-token"))
        assert r.status_code == FORBIDDEN
        body = r.json()
        assert "secret-token" not in json.dumps(body)

    def test_token_not_in_audit_log(self):
        audit_logger = AuditLogger()
        creds = CredentialRegistry()
        creds.register("secret-token", "alice", "default", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds, audit_logger=audit_logger)
        client = TestClient(app)
        client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_create"}},
                    headers=_auth_header("secret-token"))
        entries = audit_logger.entries()
        for entry in entries:
            entry_dict = entry.__dict__ if hasattr(entry, "__dict__") else str(entry)
            assert "secret-token" not in str(entry_dict)


class TestRateLimit:
    def test_rate_limit_exceeded_returns_429(self):
        config = RateLimitConfig(requests_per_second=1, requests_per_minute=3)
        limiter = RateLimiter(config)
        creds = CredentialRegistry()
        creds.register("heavy-user", "alice", "default", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds, rate_limiter=limiter)
        client = TestClient(app)

        for _ in range(3):
            r = client.post("/mcp",
                            json={"method": "tools/call", "params": {"name": "fs_read"}},
                            headers=_auth_header("heavy-user"))
            assert r.status_code == 200

        r = client.post("/mcp",
                        json={"method": "tools/call", "params": {"name": "fs_read"}},
                        headers=_auth_header("heavy-user"))
        assert r.status_code == RATE_LIMITED
        assert r.json()["code"] == "RATE_LIMITED"
        assert r.json()["retryable"] is True


class TestReadiness:
    def test_read_ready_authenticated(self):
        creds = CredentialRegistry()
        creds.register("admin-token", "admin", "default", scopes={"operate"})
        readiness = ReadinessService()
        app, _ = _make_test_app(credential_registry=creds, readiness_service=readiness)
        client = TestClient(app)

        r = client.get("/read_ready", headers=_auth_header("admin-token"))
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_write_ready_authenticated(self):
        creds = CredentialRegistry()
        creds.register("admin-token", "admin", "default", scopes={"operate"})
        readiness = ReadinessService()
        app, _ = _make_test_app(credential_registry=creds, readiness_service=readiness)
        client = TestClient(app)

        r = client.get("/write_ready", headers=_auth_header("admin-token"))
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_write_ready_false_on_writer_fence_loss(self):
        creds = CredentialRegistry()
        creds.register("admin-token", "admin", "default", scopes={"operate"})
        readiness = ReadinessService()
        readiness.set_writer_fence(False)
        app, _ = _make_test_app(credential_registry=creds, readiness_service=readiness)
        client = TestClient(app)

        r = client.get("/write_ready", headers=_auth_header("admin-token"))
        assert r.status_code == 200
        assert r.json()["status"] == "not_ready"
        assert r.json()["writer_fence"] is False

    def test_write_ready_false_on_unsupported_schema(self):
        creds = CredentialRegistry()
        creds.register("admin-token", "admin", "default", scopes={"operate"})
        readiness = ReadinessService()
        readiness.set_schema_compatible(False)
        app, _ = _make_test_app(credential_registry=creds, readiness_service=readiness)
        client = TestClient(app)

        r = client.get("/write_ready", headers=_auth_header("admin-token"))
        assert r.status_code == 200
        assert r.json()["status"] == "not_ready"
        assert r.json()["schema_write_compat"] is False

    def test_read_ready_requires_operate_scope(self):
        creds = CredentialRegistry()
        creds.register("reader", "alice", "default", scopes={"read"})
        app, _ = _make_test_app(credential_registry=creds)
        client = TestClient(app)

        r = client.get("/read_ready", headers=_auth_header("reader"))
        assert r.status_code == FORBIDDEN

    def test_livez_no_auth_ok(self):
        app, _ = _make_test_app()
        client = TestClient(app)
        r = client.get("/livez")
        assert r.status_code == 200
        assert "status" in r.json()