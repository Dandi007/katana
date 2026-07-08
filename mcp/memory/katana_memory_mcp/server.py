"""katana-memory-mcp — 可验证事实卡库的 FastMCP server（数据/实现分离）。

- 数据根：env KATANA_MEMORY_DIR（默认 /data/memory），独立 git repo，与 vault 无关。
- 多租户：每个 tenant 目录一个 FastMCP 实例，挂载在 /t/<tenant>/mcp；
  tenant 由 URL 绑定，不进 tool 签名。
- id 寻址：所有 tool 以 m-<6hex> 的 id 为接口；name 只是可读别名兼文件名。
"""
import contextlib
import os
from pathlib import Path

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from katana_memory_mcp import gitops, index as index_mod, store


def build_tenant_server(tenant: str, tenant_dir: str, repo_root: str) -> FastMCP:
    m = FastMCP(
        f"katana-memory-mcp[{tenant}]",
        instructions=(
            "可验证事实卡库（memory card）的唯一数据入口。"
            "memory_index 看 L1 清单，memory_get(id) 读全文；"
            "创建/更新走 memory_create/memory_update（正文必含 '## How to Verify' 段）。"
        ),
    )

    def _commit(action: str, result: dict) -> dict:
        msg = f"chore(memory): [{tenant}] {action} {result['id']} ({result['name']})"
        result["git"] = gitops.commit(repo_root, msg, result.pop("changed_paths"))
        return result

    @m.tool()
    async def memory_index() -> dict:
        """全量 L1 清单：每张 card 的 id/name/description/status/type/last_verified。

        含非 active 卡（status 字段自辨）；skipped 列出无法解析的文件路径。
        """
        return store.list_cards(tenant_dir)

    @m.tool()
    async def memory_get(id: str) -> dict:
        """按 id 读整卡（L1 字段 + body 全文）。

        Args:
            id: card id，形如 m-3f8a2c（见 memory_index）。
        """
        c = store.get_card(tenant_dir, id)
        if c is None:
            raise ValueError(f"card not found: {id}")
        c.pop("path", None)
        return c

    @m.tool()
    async def memory_create(name: str, description: str, body: str, type: str | None = None) -> dict:
        """建卡：服务生成并返回 id；status=active，last_verified=今天。

        Args:
            name: kebab-case 可读别名（兼文件名，可后续改）。
            description: 一行 L1 描述（SessionStart 注入用，需自包含可判读）。
            body: 正文，必含 '## Fact' 与 '## How to Verify' 段。
            type: 可选分类 user|feedback|project|reference。
        """
        return _commit("create", store.create_card(tenant_dir, name, description, body, type=type))

    @m.tool()
    async def memory_update(id: str, name: str | None = None, description: str | None = None,
                            body: str | None = None, status: str | None = None,
                            type: str | None = None, last_verified: str | None = None) -> dict:
        """按 id 改卡；None 字段不动。改 name 会同步 rename 文件（id 不变）。

        Args:
            id: card id。
            status: active|stale|deprecated（软删用 deprecated）。
            last_verified: YYYY-MM-DD；重核验后记得更新。
        """
        return _commit("update", store.update_card(
            tenant_dir, id, name=name, description=description, body=body,
            status=status, type=type, last_verified=last_verified))

    @m.tool()
    async def memory_delete(id: str) -> dict:
        """按 id 硬删卡（git 历史可恢复）；一般优先 memory_update 置 deprecated。

        Args:
            id: card id。
        """
        return _commit("delete", store.delete_card(tenant_dir, id))

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
        m = build_tenant_server(t, os.path.join(data_root, t), data_root)
        sub = m.http_app(path="/mcp")
        sub_apps.append(sub)
        mounts.append(Mount(f"/t/{t}", app=sub))

    async def index_endpoint(request):
        tenant = request.path_params["tenant"]
        if tenant not in tenants:
            return JSONResponse({"error": f"unknown tenant: {tenant}"}, status_code=404)
        cards = store.list_cards(os.path.join(data_root, tenant))["cards"]
        return JSONResponse(index_mod.hook_payload(cards, tenant))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            for sub in sub_apps:
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    return Starlette(
        routes=[Route("/t/{tenant}/index", index_endpoint), *mounts],
        lifespan=lifespan,
    )


def main() -> None:
    data_root = os.environ.get("KATANA_MEMORY_DIR", "/data/memory")
    host = os.environ.get("KATANA_MEMORY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_MEMORY_MCP_PORT", "5604"))
    uvicorn.run(build_app(data_root), host=host, port=port)


if __name__ == "__main__":
    main()
