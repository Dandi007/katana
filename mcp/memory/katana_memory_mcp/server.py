"""katana-memory-mcp — 可验证事实卡库的 FastMCP server（kernel-governed）。

- 数据根：经 katana-config.sh 解析到独立 data repo（KATANA_MEMORY_DIR env 可覆盖）。
- 多租户：每个 tenant 目录一个 FastMCP 实例，挂载在 /t/<tenant>/mcp；
  tenant 由 URL 绑定，不进 tool 签名。
- id 寻址：所有 tool 以 m-<6hex> 的 id 为接口；name 只是可读别名兼文件名。
- 治理：所有 mutation 经 GovernedKernel.mutate（CAS + policy + VFS + ledger + manifest + git）。
"""
import contextlib
import os
import re
from pathlib import Path

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from katana_kb_mcp_shared import config
from katana_kernel import (
    DomainPolicy,
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    TransactionManifest,
)
from katana_memory_mcp import index as index_mod
from katana_memory_mcp.store import MemoryStore
from katana_memory_mcp.fs_tools import FSTools


def _memory_policy() -> DomainPolicy:
    def _invariants(domain, op, args):
        if op in ("create", "update", "edit"):
            body = args.get("body")
            if body is not None:
                if not re.search(r'^## Fact\b', body, re.MULTILINE):
                    raise ValueError("body must contain '## Fact' section")
                if not re.search(r'^## How to Verify\b', body, re.MULTILINE):
                    raise ValueError("body must contain '## How to Verify' section")
        if op == "create" and not args.get("body"):
            raise ValueError("body is required for create")
        if op.startswith("fs_") and op not in ("fs_batch", "fs_capabilities", "fs_resolve",
                                                  "fs_stat", "fs_list", "fs_glob", "fs_read"):
            content = args.get("content")
            if content is not None:
                if not re.search(r'^## Fact\b', content, re.MULTILINE):
                    raise ValueError("content must contain '## Fact' section")
                if not re.search(r'^## How to Verify\b', content, re.MULTILINE):
                    raise ValueError("content must contain '## How to Verify' section")

    return DomainPolicy(
        domain="memory",
        allowed_ops={"create", "update", "delete", "edit", "list", "get", "read",
            "fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename",
            "fs_delete", "fs_batch"},
        invariants=[_invariants],
    )


def _resolve_data_root() -> str:
    root = os.environ.get("KATANA_MEMORY_DIR")
    if root:
        return root
    try:
        resolved = config.resolve("memory_data_path", default="", env_var="KATANA_MEMORY_DIR")
        if resolved and resolved != ".":
            return resolved
    except Exception:
        pass
    raise RuntimeError(
        "KATANA_MEMORY_DIR not set and katana-config.sh could not resolve memory_data_path; "
        "set KATANA_MEMORY_DIR to the data repo root."
    )


def build_tenant_server(tenant: str, tenant_dir: str, repo_root: str,
                         kernel: GovernedKernel | None = None) -> FastMCP:
    m = FastMCP(
        f"katana-memory-mcp[{tenant}]",
        instructions=(
            "可验证事实卡库（memory card）的唯一数据入口。"
            "读：memory_index 看 L1 清单；memory_get(id) 取结构化整卡；"
            "memory_read(id) 取原始文件文本（FS-Read 语义，cat -n + offset/limit，"
            "构造 memory_edit 的 old_string 前先用它拿精确文本）。"
            "写：memory_create/memory_update（整字段替换，正文必含 '## How to Verify'）；"
            "局部改用 memory_edit（FS-Edit 语义，old_string->new_string，先 memory_read，比整篇重写省 token）。"
        ),
    )

    if kernel is None:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        ledger = ResourceIdLedger(os.path.join(repo_root, ".katana", "tombstones.json"))
        manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        policy = _memory_policy()
        kernel.bind("memory", policy, vfs, ledger, manifest, repo_root)
    store = MemoryStore(kernel)
    fs_tools = FSTools(kernel, tenant, repo_root)

    @m.tool()
    async def memory_index() -> dict:
        return store.list_cards(tenant)

    @m.tool()
    async def memory_get(id: str) -> dict:
        c = store.get_card(tenant, id)
        if c is None:
            raise ValueError(f"card not found: {id}")
        c.pop("path", None)
        return c

    @m.tool()
    async def memory_create(name: str, description: str, body: str,
                            type: str | None = None,
                            expected_base_sha: str | None = None) -> dict:
        return store.create_card(tenant, name, description, body, type=type,
                                 expected_base_sha=expected_base_sha)

    @m.tool()
    async def memory_update(id: str, name: str | None = None, description: str | None = None,
                            body: str | None = None, status: str | None = None,
                            type: str | None = None, last_verified: str | None = None,
                            expected_base_sha: str | None = None) -> dict:
        return store.update_card(tenant, id, name=name, description=description, body=body,
                                 status=status, type=type, last_verified=last_verified,
                                 expected_base_sha=expected_base_sha)

    @m.tool()
    async def memory_delete(id: str, expected_base_sha: str | None = None) -> dict:
        return store.delete_card(tenant, id, expected_base_sha=expected_base_sha)

    @m.tool()
    async def memory_read(id: str, offset: int | None = None, limit: int | None = None) -> dict:
        return store.read_card_raw(tenant, id, offset=offset, limit=limit)

    @m.tool()
    async def memory_edit(id: str, old_string: str, new_string: str,
                           replace_all: bool = False,
                           expected_base_sha: str | None = None) -> dict:
        return store.edit_card(tenant, id, old_string, new_string, replace_all=replace_all,
                                expected_base_sha=expected_base_sha)

    # ── fs_* Full VFS tools ──────────────────────────────────────────────────

    @m.tool()
    async def fs_capabilities() -> dict:
        return fs_tools.fs_capabilities()

    @m.tool()
    async def fs_resolve(path_or_id: str) -> dict:
        return fs_tools.fs_resolve(path_or_id)

    @m.tool()
    async def fs_stat(path: str) -> dict:
        return fs_tools.fs_stat(path)

    @m.tool()
    async def fs_list(path: str = "") -> dict:
        return fs_tools.fs_list(path)

    @m.tool()
    async def fs_glob(pattern: str) -> dict:
        return fs_tools.fs_glob(pattern)

    @m.tool()
    async def fs_read(path: str, offset: int | None = None,
                       limit: int | None = None) -> dict:
        return fs_tools.fs_read(path, offset=offset, limit=limit)

    @m.tool()
    async def fs_create(path: str, content: str,
                        resource_id: str | None = None,
                        expected_base_sha: str | None = None,
                        idempotency_key: str | None = None) -> dict:
        return fs_tools.fs_create(path, content, resource_id=resource_id,
                                  expected_base_sha=expected_base_sha,
                                  idempotency_key=idempotency_key)

    @m.tool()
    async def fs_write(path: str, content: str,
                       resource_id: str | None = None,
                       expected_base_sha: str | None = None,
                       expected_resource_revision: str | None = None,
                       idempotency_key: str | None = None) -> dict:
        return fs_tools.fs_write(path, content, resource_id=resource_id,
                                 expected_base_sha=expected_base_sha,
                                 expected_resource_revision=expected_resource_revision,
                                 idempotency_key=idempotency_key)

    @m.tool()
    async def fs_edit(path: str, old_string: str, new_string: str,
                      resource_id: str | None = None,
                      replace_all: bool = False,
                      expected_base_sha: str | None = None,
                      expected_resource_revision: str | None = None,
                      idempotency_key: str | None = None) -> dict:
        return fs_tools.fs_edit(path, old_string, new_string,
                                resource_id=resource_id,
                                replace_all=replace_all,
                                expected_base_sha=expected_base_sha,
                                expected_resource_revision=expected_resource_revision,
                                idempotency_key=idempotency_key)

    @m.tool()
    async def fs_copy(source: str, dest: str,
                      resource_id: str | None = None,
                      expected_base_sha: str | None = None,
                      idempotency_key: str | None = None) -> dict:
        return fs_tools.fs_copy(source, dest, resource_id=resource_id,
                                expected_base_sha=expected_base_sha,
                                idempotency_key=idempotency_key)

    @m.tool()
    async def fs_rename(source: str, dest: str,
                        resource_id: str | None = None,
                        expected_base_sha: str | None = None,
                        idempotency_key: str | None = None) -> dict:
        return fs_tools.fs_rename(source, dest, resource_id=resource_id,
                                  expected_base_sha=expected_base_sha,
                                  idempotency_key=idempotency_key)

    @m.tool()
    async def fs_delete(path: str,
                        resource_id: str | None = None,
                        expected_base_sha: str | None = None,
                        idempotency_key: str | None = None) -> dict:
        return fs_tools.fs_delete(path, resource_id=resource_id,
                                  expected_base_sha=expected_base_sha,
                                  idempotency_key=idempotency_key)

    @m.tool()
    async def fs_batch(operations: list[dict],
                       expected_base_commit: str | None = None,
                       idempotency_key: str | None = None) -> dict:
        return fs_tools.fs_batch(operations, expected_base_commit=expected_base_commit,
                                  idempotency_key=idempotency_key)

    return m


def _tenants(data_root: str) -> list[str]:
    root = Path(data_root)
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith("."))


def build_app(data_root: str) -> Starlette:
    tenants = _tenants(data_root)

    kernel = GovernedKernel()
    vfs = GovernedVFS(data_root)
    ledger = ResourceIdLedger(os.path.join(data_root, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(data_root, ".katana", "manifests"))
    policy = _memory_policy()
    kernel.bind("memory", policy, vfs, ledger, manifest, data_root)

    sub_apps: list = []
    mounts: list = []
    for t in tenants:
        mcp_server = build_tenant_server(t, os.path.join(data_root, t), data_root, kernel=kernel)
        sub = mcp_server.http_app(path="/mcp")
        sub_apps.append(sub)
        mounts.append(Mount(f"/t/{t}", app=sub))

    def _tenant_cards(tenant: str) -> list[dict] | None:
        if tenant not in tenants:
            return None
        from katana_memory_mcp import store as _raw_store
        return _raw_store.list_cards(os.path.join(data_root, tenant))["cards"]

    async def index_endpoint(request):
        tenant = request.path_params["tenant"]
        cards = _tenant_cards(tenant)
        if cards is None:
            return JSONResponse({"error": f"unknown tenant: {tenant}"}, status_code=404)
        return JSONResponse(index_mod.hook_payload(cards, tenant))

    async def index_md_endpoint(request):
        tenant = request.path_params["tenant"]
        cards = _tenant_cards(tenant)
        if cards is None:
            return PlainTextResponse(f"unknown tenant: {tenant}", status_code=404)
        return PlainTextResponse(index_mod.render_index(cards, tenant))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            for sub in sub_apps:
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    return Starlette(
        routes=[
            Route("/t/{tenant}/index", index_endpoint),
            Route("/t/{tenant}/index.md", index_md_endpoint),
            *mounts,
        ],
        lifespan=lifespan,
    )


def build_remote_app(
    data_root: str,
    credential_registry: "CredentialRegistry",
    *,
    rate_limiter=None,
    readiness_service=None,
    audit_logger=None,
    tenant_resolver=None,
) -> Starlette:
    """Build the memory app with remote auth middleware applied.

    Returns a Starlette app with all routes (index, index.md, per-tenant MCP)
    wrapped in the remote auth layer.
    """
    from katana_remote import AuthMiddleware, RateLimiter, ReadinessService, AuditLogger

    inner = build_app(data_root)
    rate_limiter = rate_limiter or RateLimiter()
    readiness_service = readiness_service or ReadinessService()
    audit_logger = audit_logger or AuditLogger()

    return AuthMiddleware(
        inner,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain="memory",
    )


def main() -> None:
    data_root = _resolve_data_root()
    host = os.environ.get("KATANA_MEMORY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_MEMORY_MCP_PORT", "5605"))
    uvicorn.run(build_app(data_root), host=host, port=port)


if __name__ == "__main__":
    main()