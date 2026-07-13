"""Remote auth middleware: ASGI middleware that wraps FastMCP apps with auth.

Design §5.1 / §7.1 / §7.2 / §7.5 / §7.3
"""

from __future__ import annotations

import json
from typing import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from katana_remote.auth import (
    CredentialRegistry,
    extract_bearer_token,
    UNAUTHORIZED,
    FORBIDDEN,
    RATE_LIMITED,
)
from katana_remote.scopes import requires_scope
from katana_remote.tenant import TenantResolver, validate_tenant_match
from katana_remote.ratelimit import RateLimiter
from katana_remote.readiness import ReadinessService
from katana_remote.audit import AuditLogger, audit_log


def _extract_tenant_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "t":
        return parts[1]
    return None


def _extract_operation_from_body(body: dict | None) -> str | None:
    if body and "method" in body:
        m = body.get("method", "")
        if m.startswith("tools/call"):
            params = body.get("params", {})
            name = params.get("name", "")
            if name:
                return name
        return m
    return None


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
    ):
        self._app = app
        self._credentials = credential_registry
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self._readiness = readiness_service if readiness_service is not None else ReadinessService()
        self._audit = audit_logger if audit_logger is not None else AuditLogger()
        self._tenants = tenant_resolver if tenant_resolver is not None else TenantResolver()
        self._domain = domain
        self._policy_version = policy_version

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return

        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path.rstrip("/") or "/"

        if path == "/livez":
            response = JSONResponse(self._readiness.livez())
            await response(scope, receive, send)
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
            self._log_audit(principal, "read_ready", result="success")
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
            self._log_audit(principal, "write_ready", result="success")
            await r(scope, receive, send)
            return

        if path == "/audit_query":
            if not requires_scope(principal.scopes, "audit_query"):
                response = _build_error_response(
                    FORBIDDEN, "FORBIDDEN",
                    "insufficient scope for audit")
                await response(scope, receive, send)
                return
            entries_dict = []
            for e in self._audit.entries():
                entries_dict.append({
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
                })
            r = JSONResponse({"entries": entries_dict})
            self._log_audit(principal, "audit_query", result="success")
            await r(scope, receive, send)
            return

        body = await request.body()
        body_obj = None
        if body:
            try:
                body_obj = json.loads(body)
            except Exception:
                body_obj = None

        operation = _extract_operation_from_body(body_obj)
        if operation is None:
            parts = path.strip("/").split("/")
            operation = parts[-1] if parts else "unknown"

        if not requires_scope(principal.scopes, operation):
            self._log_audit(principal, operation,
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
            self._log_audit(principal, operation,
                            error="rate limited", result="error")
            response = _build_error_response(
                RATE_LIMITED, "RATE_LIMITED",
                "rate limit exceeded, retry later",
                retryable=True)
            await response(scope, receive, send)
            return

        _sent = False
        original_receive = receive

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

        _response_started = False
        _response_status = 200
        original_send = send

        async def _wrapped_send(message: dict) -> None:
            nonlocal _response_started, _response_status
            if message["type"] == "http.response.start":
                _response_started = True
                _response_status = message.get("status", 200)
            await original_send(message)

        await self._app(scope, _wrapped_receive, _wrapped_send)

        if _response_started and _response_status >= 400:
            self._log_audit(principal, operation,
                            error=f"HTTP {_response_status}", result="error")
        else:
            self._log_audit(principal, operation, result="success")

    def _log_audit(self, principal, operation: str,
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
    """Wrap an ASGI app with auth middleware, returning a Starlette app.

    The returned Starlette app properly handles lifespan events for
    FastMCP's session manager.
    """
    import contextlib

    middleware = AuthMiddleware(
        inner_app,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain=domain,
    )

    @contextlib.asynccontextmanager
    async def _lifespan(app):
        if hasattr(inner_app, "router") and hasattr(inner_app.router, "lifespan_context"):
            async with inner_app.router.lifespan_context(inner_app):
                yield
        else:
            yield

    from starlette.routing import Mount
    return Starlette(
        lifespan=_lifespan,
        routes=[Mount("/", app=middleware)],
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
    """Wrap a FastMCP instance with auth middleware, returning a Starlette app."""
    inner_app = fastmcp_instance.http_app() if hasattr(fastmcp_instance, "http_app") else fastmcp_instance
    return create_remote_app(
        inner_app,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain=domain,
    )