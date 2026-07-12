"""katana-memory-mcp — 可验证事实卡库的 FastMCP server（kernel-governed）。

- 数据根：经 katana-config.sh 解析到独立 data repo（KATANA_MEMORY_DIR env 可覆盖）。
- 多租户：每个 tenant 目录一个 FastMCP 实例，挂载在 /t/<tenant>/mcp；
  tenant 由 URL 绑定，不进 tool 签名。
- id 寻址：所有 tool 以 m-<6hex> 的 id 为接口；name 只是可读别名兼文件名。
- 治理：所有 mutation 经 GovernedKernel.mutate（CAS + policy + VFS + ledger + manifest + git）。
"""
import contextlib
import os
from pathlib import Path

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from katana_kb_mcp_shared import config
from katana_kernel import (
    CASRejectionError,
    DomainPolicy,
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    TransactionManifest,
    head_sha,
    is_working_tree_clean,
)
from katana_memory_mcp import index as index_mod
from katana_memory_mcp.store import MemoryStore


def _memory_policy() -> DomainPolicy:
    def _invariants(domain, op, args):
        if op in ("create", "update", "edit"):
            body = args.get("body")
            if body is not None:
                if "## Fact" not in body:
                    raise ValueError("body must contain '## Fact' section")
                if "## How to Verify" not in body:
                    raise ValueError("body must contain '## How to Verify' section")
        if op == "create" and not args.get("body"):
            raise ValueError("body is required for create")

    return DomainPolicy(
        domain="memory",
        allowed_ops={"create", "update", "delete", "edit", "list", "get", "read"},
        invariants=[_invariants],
    )


def _resolve_data_root() -> str:
    root = os.environ.get("KATANA_MEMORY_DIR")
    if root:
        return root
    try:
        return config.resolve("memory_data_path", default=".", env_var="KATANA_MEMORY_DIR")
    except Exception:
        return "."


def build_tenant_server(tenant: str, tenant_dir: str, repo_root: str) -> FastMCP:
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

    kernel = GovernedKernel()
    vfs = GovernedVFS(repo_root)
    ledger = ResourceIdLedger(os.path.join(repo_root, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
    policy = _memory_policy()
    kernel.bind("memory", policy, vfs, ledger, manifest, repo_root)
    store = MemoryStore(kernel)

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

    return m


def _tenants(data_root: str) -> list[str]:
    root = Path(data_root)
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and not d.name.startswith("."))


def build_app(data_root: str) -> Starlette:
    tenants = _tenants(data_root)
    sub_apps: list = []
    mounts: list = []
    for t in tenants:
        mcp_server = build_tenant_server(t, os.path.join(data_root, t), data_root)
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


def main() -> None:
    data_root = _resolve_data_root()
    host = os.environ.get("KATANA_MEMORY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_MEMORY_MCP_PORT", "5605"))
    uvicorn.run(build_app(data_root), host=host, port=port)


if __name__ == "__main__":
    main()