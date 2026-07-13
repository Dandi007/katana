"""Remote auth middleware: ASGI middleware that wraps FastMCP apps with auth.

Integrates bearer authentication, scope enforcement, tenant confinement,
rate limiting, readiness probes, and audit logging into a single ASGI
middleware layer. Designed to work with FastMCP's streamable-http transport
and Starlette-based apps.

Design §5.1 / §7.1 / §7.2 / §7.5 / §7.3
"""

from __future__ import annotations

import json
from typing import Any, Callable, Awaitable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from katana_remote.auth import (
    CredentialRegistry,
    extract_bearer_token,
    UNAUTHORIZED,
    FORBIDDEN,
    RATE_LIMITED,
)
from katana_remote.scopes import requires_scope
from katana_remote.tenant import TenantResolver, validate_tenant_match
from katana_remote.ratelimit import RateLimiter, RateLimitConfig
from katana_remote.readiness import ReadinessService
from katana_remote.audit import AuditLogger, audit_log


def _extract_tenant_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "t":
        return parts[1]
    return None


def _extract_operation_from_path_and_body(path: str, body: dict | None) -> str:
    parts = path.strip("/").split("/")
    if body and "method" in body:
        m = body.get("method", "")
        if m.startswith("tools/call"):
            params = body.get("params", {})
            name = params.get("name", "")
            if name:
                return name
        return m
    if len(parts) >= 3 and parts[0] == "t":
        return parts[-1]
    return parts[-1] if parts else "unknown"


def _build_error_response(status_code: int, code: str, message: str,
                          retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "retryable": retryable},
        status_code=status_code,
    )


class AuthMiddleware:
    def __init__(
        self,
        app,
        credential_registry: CredentialRegistry,
        *,
        rate_limiter: RateLimiter | None = None,
        readiness_service: ReadinessService | None = None,
        audit_logger: AuditLogger | None = None,
        tenant_resolver: TenantResolver | None = None,
        domain: str = "default",
        policy_version: str = "1.0",
        public_paths: set[str] | None = None,
    ):
        self._app = app
        self._credentials = credential_registry
        self._rate_limiter = rate_limiter or RateLimiter()
        self._readiness = readiness_service or ReadinessService()
        self._audit = audit_logger or AuditLogger()
        self._tenants = tenant_resolver or TenantResolver()
        self._domain = domain
        self._policy_version = policy_version
        self._public_paths = public_paths or set()

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path.rstrip("/") or "/"

        if path == "/livez":
            response = JSONResponse(self._readiness.livez())
            await response(scope, receive, send)
            return

        if path in self._public_paths:
            await self._app(scope, receive, send)
            return

        auth_header = dict(request.headers)
        token = extract_bearer_token(auth_header)

        if token is None:
            response = _build_error_response(
                UNAUTHORIZED, "UNAUTHORIZED",
                "missing or invalid Authorization header")
            await response(scope, receive, send)
            return

        auth_token = self._credentials.authenticate(token)
        if auth_token is None:
            response = _build_error_response(
                UNAUTHORIZED, "UNAUTHORIZED",
                "invalid, expired, or revoked token")
            await response(scope, receive, send)
            return

        principal = auth_token.principal
        url_tenant = _extract_tenant_from_path(path)
        if not validate_tenant_match(principal.tenant, url_tenant):
            response = _build_error_response(
                FORBIDDEN, "TENANT_MISMATCH",
                f"URL tenant {url_tenant} does not match credential tenant {principal.tenant}")
            await response(scope, receive, send)
            return

        if path == "/read_ready":
            if not requires_scope(principal.scopes, "read_ready"):
                response = _build_error_response(
                    FORBIDDEN, "FORBIDDEN",
                    "insufficient scope for read_ready")
                await response(scope, receive, send)
                return
            r = JSONResponse(self._readiness.read_ready())
            self._log_audit(principal, "read_ready", "readiness", result="success")
            await r(scope, receive, send)
            return

        if path == "/write_ready":
            if not requires_scope(principal.scopes, "write_ready"):
                response = _build_error_response(
                    FORBIDDEN, "FORBIDDEN",
                    "insufficient scope for write_ready")
                await response(scope, receive, send)
                return
            r = JSONResponse(self._readiness.write_ready())
            self._log_audit(principal, "write_ready", "readiness", result="success")
            await r(scope, receive, send)
            return

        if path == "/audit_query":
            if not requires_scope(principal.scopes, "audit_query"):
                response = _build_error_response(
                    FORBIDDEN, "FORBIDDEN",
                    "insufficient scope for audit")
                await response(scope, receive, send)
                return
            entries = [e.__dict__ if hasattr(e, "__dict__") else str(e) for e in self._audit.entries()]
            entries_dict = []
            for e in self._audit.entries():
                d = {
                    "request_id": e.request_id,
                    "mutation_id": e.mutation_id,
                    "principal_id": e.principal_id,
                    "tenant": e.tenant,
                    "domain": e.domain,
                    "scopes": e.scopes,
                    "operation": e.operation,
                    "resource_ids": e.resource_ids,
                    "result": e.result,
                    "error": e.error,
                    "server_time": e.server_time,
                }
                entries_dict.append(d)
            r = JSONResponse({"entries": entries_dict})
            self._log_audit(principal, "audit_query", "audit", result="success")
            await r(scope, receive, send)
            return

        body = await request.body()
        body_obj = None
        if body:
            try:
                body_obj = json.loads(body)
            except Exception:
                body_obj = None

        operation = _extract_operation_from_path_and_body(path, body_obj)

        if not requires_scope(principal.scopes, operation):
            self._log_audit(principal, operation, "mcp",
                            error="insufficient scope", result="error")
            response = _build_error_response(
                FORBIDDEN, "FORBIDDEN",
                f"scope {principal.scopes} does not allow operation {operation}")
            await response(scope, receive, send)
            return

        is_mutation = operation in {
            "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename", "fs_delete",
            "fs_batch", "memory_create", "memory_update", "memory_delete", "memory_edit",
            "wiki_ingest_plan", "wiki_ingest_apply",
            "wf_create", "wf_save", "wf_resume", "wf_reindex",
        }
        is_batch = operation == "fs_batch"

        if not self._rate_limiter.check(
            principal.principal_id, principal.tenant,
            is_mutation=is_mutation, is_batch=is_batch,
        ):
            self._log_audit(principal, operation, "mcp",
                            error="rate limited", result="error")
            response = _build_error_response(
                RATE_LIMITED, "RATE_LIMITED",
                "rate limit exceeded, retry later",
                retryable=True)
            await response(scope, receive, send)
            return

        async def _wrapped_receive() -> dict:
            nonlocal _sent
            if not _sent:
                _sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return await original_receive()

        _sent = False
        original_receive = receive

        try:
            await self._app(scope, _wrapped_receive, send)
        except Exception as e:
            self._log_audit(principal, operation, "mcp",
                            error=str(e), result="error")
            error_response = _build_error_response(500, "INTERNAL_ERROR", str(e))
            await error_response(scope, receive, send)
            return

        self._log_audit(principal, operation, "mcp", result="success")

    def _log_audit(self, principal, operation: str, endpoint_type: str,
                   result: str = "success", error: str | None = None) -> None:
        audit_log(
            self._audit,
            principal_id=principal.principal_id,
            tenant=principal.tenant,
            domain=self._domain,
            scopes=sorted(principal.scopes),
            operation=operation,
            result=result,
            error=error,
            policy_version=self._policy_version,
        )


def create_remote_app(
    inner_app,
    credential_registry: CredentialRegistry,
    *,
    tenant_resolver: TenantResolver | None = None,
    rate_limiter: RateLimiter | None = None,
    readiness_service: ReadinessService | None = None,
    audit_logger: AuditLogger | None = None,
    domain: str = "default",
):
    return AuthMiddleware(
        inner_app,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain=domain,
    )


def wrap_fastmcp(
    fastmcp_instance,
    credential_registry: CredentialRegistry,
    *,
    tenant_resolver: TenantResolver | None = None,
    rate_limiter: RateLimiter | None = None,
    readiness_service: ReadinessService | None = None,
    audit_logger: AuditLogger | None = None,
    domain: str = "default",
):
    inner_app = fastmcp_instance.http_app() if hasattr(fastmcp_instance, "http_app") else fastmcp_instance
    return AuthMiddleware(
        inner_app,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain=domain,
    )