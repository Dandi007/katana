"""Remote auth middleware: ASGI middleware that wraps FastMCP apps with auth.

Design §5.1 / §7.1 / §7.2 / §7.5 / §7.3
"""

from __future__ import annotations

import hashlib
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
from katana_remote.scopes import requires_scope, scope_required_for_operation
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


def _extract_tenant_from_body_args(body: dict | None) -> str | None:
    if body and "method" in body:
        m = body.get("method", "")
        params = body.get("params", {})
        if m.startswith("tools/call"):
            args = params.get("arguments", {})
            if isinstance(args, dict) and "tenant" in args:
                return args["tenant"]
        if isinstance(params, dict) and "tenant" in params:
            return params["tenant"]
    return None


def _extract_is_recursive(body: dict | None) -> bool:
    if body and "method" in body:
        params = body.get("params", {})
        if isinstance(params, dict):
            args = params.get("arguments", {})
            if isinstance(args, dict):
                return bool(args.get("recursive", False))
    return False


def _extract_resource_count(body: dict | None) -> int:
    if body and "method" in body:
        params = body.get("params", {})
        if isinstance(params, dict):
            args = params.get("arguments", {})
            if isinstance(args, dict):
                ops = args.get("operations", [])
                if isinstance(ops, list):
                    return len(ops)
    return 0


def _namespace_idempotency_key(
    body: dict | None,
    *,
    domain: str,
    tenant: str,
    principal_id: str,
) -> bool:
    """Bind remote idempotency to the authenticated caller namespace."""

    if not isinstance(body, dict) or not str(body.get("method", "")).startswith(
        "tools/call"
    ):
        return False
    params = body.get("params")
    if not isinstance(params, dict):
        return False
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return False
    raw_key = arguments.get("idempotency_key")
    if (
        not isinstance(raw_key, str)
        or not raw_key.strip()
        or len(raw_key) > 256
    ):
        return False
    material = "\0".join(
        ("katana-remote-idempotency-v1", domain, tenant, principal_id, raw_key)
    ).encode("utf-8")
    arguments["idempotency_key"] = (
        "remote-v1:" + hashlib.sha256(material).hexdigest()
    )
    return True


def _scope_with_content_length(scope: dict, length: int) -> dict:
    updated = dict(scope)
    headers = [
        (name, value)
        for name, value in scope.get("headers", [])
        if name.lower() != b"content-length"
    ]
    headers.append((b"content-length", str(length).encode("ascii")))
    updated["headers"] = headers
    return updated


def _build_error_response(status_code: int, code: str, message: str,
                          retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "retryable": retryable},
        status_code=status_code,
    )


def _parse_response_body_for_audit(response_body: bytes) -> dict:
    result = {}
    try:
        text = response_body.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except Exception:
                    continue
                if isinstance(data, dict):
                    _extract_audit_fields(data, result)
                continue
        try:
            data = json.loads(text)
        except Exception:
            return result
        if isinstance(data, dict):
            _extract_audit_fields(data, result)
    except Exception:
        pass
    return result


def _extract_audit_fields(data: dict, result: dict) -> None:
    if "result" in data:
        inner = data["result"]
        if isinstance(inner, dict):
            content = inner.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        try:
                            tool_result = json.loads(item["text"])
                            if isinstance(tool_result, dict):
                                if "commit" in tool_result and "resulting_commit" not in result:
                                    result["resulting_commit"] = tool_result["commit"]
                                elif "git" in tool_result and isinstance(tool_result["git"], dict):
                                    if "detail" in tool_result["git"] and "resulting_commit" not in result:
                                        result["resulting_commit"] = tool_result["git"]["detail"]
                                if "resource_id" in tool_result and "resource_ids" not in result:
                                    result["resource_ids"] = [tool_result["resource_id"]]
                                if "mutation_id" in tool_result and "mutation_id" not in result:
                                    result["mutation_id"] = tool_result["mutation_id"]
                                if "base_commit" in tool_result and "base_commit" not in result:
                                    result["base_commit"] = tool_result["base_commit"]
                        except Exception:
                            pass


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
        client_identity = request.headers.get("user-agent", "unknown")

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
            self._log_audit(principal, "read_ready", result="success",
                            client_identity=client_identity)
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
            self._log_audit(principal, "write_ready", result="success",
                            client_identity=client_identity)
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
            self._log_audit(principal, "audit_query", result="success",
                            client_identity=client_identity)
            await r(scope, receive, send)
            return

        if path == "/audit_export":
            if not requires_scope(principal.scopes, "audit_export"):
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
            self._log_audit(principal, "audit_export", result="success",
                            client_identity=client_identity)
            await r(scope, receive, send)
            return

        body = await request.body()
        body_obj = None
        if body:
            try:
                body_obj = json.loads(body)
            except Exception:
                body_obj = None

        body_tenant = _extract_tenant_from_body_args(body_obj)
        if body_tenant is not None and body_tenant != principal.tenant:
            self._log_audit(principal, "unknown",
                            error="body/tool-arg tenant mismatch", result="error",
                            client_identity=client_identity)
            response = _build_error_response(
                FORBIDDEN, "TENANT_MISMATCH",
                f"body/tool-arg tenant {body_tenant} does not match credential tenant {principal.tenant}")
            await response(scope, receive, send)
            return

        operation = _extract_operation_from_body(body_obj)
        if operation is None:
            parts = path.strip("/").split("/")
            operation = parts[-1] if parts else "unknown"

        required_scope = scope_required_for_operation(operation)
        if required_scope is None:
            self._log_audit(principal, operation,
                            error="unmapped operation", result="error",
                            client_identity=client_identity)
            response = _build_error_response(
                FORBIDDEN, "FORBIDDEN",
                f"operation {operation} is not mapped to any scope")
            await response(scope, receive, send)
            return

        if not requires_scope(principal.scopes, operation):
            self._log_audit(principal, operation,
                            error="insufficient scope", result="error",
                            client_identity=client_identity)
            response = _build_error_response(
                FORBIDDEN, "FORBIDDEN",
                f"scope {principal.scopes} does not allow operation {operation}")
            await response(scope, receive, send)
            return

        is_mutation = operation in {
            "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename", "fs_delete",
            "fs_batch", "memory_create", "memory_update", "memory_delete", "memory_edit",
            "wiki_ingest_plan", "wiki_ingest_apply",
            "wf_create", "wf_save", "wf_resume", "wf_append_progress", "wf_reindex",
        }
        is_batch = operation == "fs_batch"
        is_recursive = _extract_is_recursive(body_obj)
        resource_count = _extract_resource_count(body_obj)

        if not self._rate_limiter.check(
            principal.principal_id, principal.tenant,
            is_mutation=is_mutation, is_batch=is_batch,
            resource_count=resource_count, is_recursive=is_recursive,
        ):
            self._log_audit(principal, operation,
                            error="rate limited", result="error",
                            client_identity=client_identity)
            response = _build_error_response(
                RATE_LIMITED, "RATE_LIMITED",
                "rate limit exceeded, retry later",
                retryable=True)
            await response(scope, receive, send)
            return

        if _namespace_idempotency_key(
            body_obj,
            domain=self._domain,
            tenant=principal.tenant,
            principal_id=principal.principal_id,
        ):
            body = json.dumps(
                body_obj,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            scope = _scope_with_content_length(scope, len(body))

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
        _response_body_chunks: list[bytes] = []
        original_send = send

        async def _wrapped_send(message: dict) -> None:
            nonlocal _response_started, _response_status
            if message["type"] == "http.response.start":
                _response_started = True
                _response_status = message.get("status", 200)
            elif message["type"] == "http.response.body":
                body_chunk = message.get("body", b"")
                if body_chunk:
                    _response_body_chunks.append(body_chunk)
            await original_send(message)

        await self._app(scope, _wrapped_receive, _wrapped_send)

        response_body = b"".join(_response_body_chunks)
        audit_extra = _parse_response_body_for_audit(response_body)

        if _response_started and _response_status >= 400:
            self._log_audit(principal, operation,
                            error=f"HTTP {_response_status}", result="error",
                            client_identity=client_identity,
                            **audit_extra)
        else:
            self._log_audit(principal, operation, result="success",
                            client_identity=client_identity,
                            **audit_extra)

    def _log_audit(self, principal, operation: str,
                   result: str = "success", error: str | None = None,
                   client_identity: str = "unknown",
                   resource_ids: list[str] | None = None,
                   base_commit: str | None = None,
                   resulting_commit: str | None = None,
                   mutation_id: str | None = None,
                   **extra) -> None:
        audit_log(
            self._audit,
            principal_id=principal.principal_id,
            tenant=principal.tenant,
            domain=self._domain,
            scopes=sorted(principal.scopes),
            operation=operation,
            resource_ids=resource_ids,
            base_commit=base_commit,
            resulting_commit=resulting_commit,
            mutation_id=mutation_id,
            result=result,
            error=error,
            client_identity=client_identity,
            policy_version=self._policy_version,
            **extra,
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
