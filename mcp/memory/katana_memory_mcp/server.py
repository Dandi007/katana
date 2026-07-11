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
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from katana_kb_mcp_shared.kernel import (
    Catalog, GovernedVFS, KernelError, TransactionEngine,
)
from katana_kb_mcp_shared.kernel.policy import AppComposition

from katana_memory_mcp import gitops, index as index_mod, store
from katana_memory_mcp.policy import ID_PREFIX, MemoryPolicy


def build_tenant_server(tenant: str, tenant_dir: str, repo_root: str) -> FastMCP:
    m = FastMCP(
        f"katana-memory-mcp[{tenant}]",
        instructions=(
            "可验证事实卡库（memory card）的唯一数据入口。"
            "读：memory_index 看 L1 清单；memory_get(id) 取结构化整卡；"
            "memory_read(id) 取原始文件文本（FS-Read 语义，cat -n + offset/limit，"
            "构造 memory_edit 的 old_string 前先用它拿精确文本）。"
            "写：memory_create/memory_update（整字段替换，正文必含 '## How to Verify'）；"
            "局部改用 memory_edit（FS-Edit 语义，old_string→new_string，先 memory_read，比整篇重写省 token）。"
        ),
    )

    def _commit(action: str, result: dict) -> dict:
        msg = f"chore(memory): [{tenant}] {action} {result['id']} ({result['name']})"
        result["git"] = gitops.commit(repo_root, msg, result.pop("changed_paths"))
        return result

    # Governed Full VFS composition root (design §4.2/§5.2): fs_* shares the same
    # policy → transaction pipeline as the 7 domain tools; no raw bypass (INV-10).
    _composition = AppComposition(MemoryPolicy())
    _engine = TransactionEngine(repo_root, domain="memory",
                                policy_version=_composition.policy.policy_version)
    _catalog = Catalog(repo_root, id_prefix=ID_PREFIX)
    _vfs = GovernedVFS(_engine, _catalog, _composition.policy)

    def _scoped(path: str) -> str:
        return path if path == tenant or path.startswith(f"{tenant}/") \
            else f"{tenant}/{path}"

    def _guard(fn, *a, **k):
        try:
            return fn(*a, **k)
        except KernelError as e:
            raise ValueError(e.to_envelope()) from e

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

    @m.tool()
    async def memory_read(id: str, offset: int | None = None, limit: int | None = None) -> dict:
        """按 id 读卡的原始文件文本（frontmatter + body），cat -n 行号，支持 offset/limit 分页。

        语义同 FS Read，入参为 card id（非路径）。读大卡可只取片段。
        构造 memory_edit 的 old_string 前，先用它拿到精确文本（含 frontmatter）。
        与 memory_get 区别：get 返回结构化字段，read 返回原始文本。

        Args:
            id: card id。
            offset: 1-based 起始行（默认 1）。
            limit: 读取行数（默认到文件尾）。
        """
        return store.read_card_raw(tenant_dir, id, offset=offset, limit=limit)

    @m.tool()
    async def memory_edit(id: str, old_string: str, new_string: str,
                          replace_all: bool = False) -> dict:
        """按 id 对卡做精确字符串替换（old_string→new_string），语义同 FS Edit，入参 card id。

        先 memory_read 拿精确文本。old_string 须精确匹配（空白敏感）且唯一；
        多次命中需 replace_all=True。改 body 局部用它，比 memory_update 整篇重写省 token。
        写走治理路径（校验结果可解析 + id 不可变 + 原子写 + git commit）。
        改 name 字段会同步 rename 文件；改 id 会被拒。

        Args:
            id: card id。
            old_string: 要替换的精确子串（唯一，或 replace_all）。
            new_string: 替换为的文本。
            replace_all: True 时替换全部命中。
        """
        return _commit("edit", store.edit_card(
            tenant_dir, id, old_string, new_string, replace_all=replace_all))

    # ── Governed Full VFS façade (fs_*) ──────────────────────────────────────
    @m.tool()
    async def fs_read(virtual_path: str, offset: int | None = None,
                      limit: int | None = None) -> dict:
        """治理 VFS 读（canonical tree，cat -n）；path 相对 tenant 根。"""
        return _guard(_vfs.fs_read, virtual_path=_scoped(virtual_path),
                      offset=offset, limit=limit)

    @m.tool()
    async def fs_list(virtual_path: str = "") -> list[dict]:
        """列出 tenant 子树节点（reserved namespace 隐藏）。"""
        return _guard(_vfs.fs_list,
                      _scoped(virtual_path) if virtual_path else tenant)

    @m.tool()
    async def fs_stat(virtual_path: str) -> dict:
        """节点统一 descriptor（id/path/hash/revision/snapshot commit）。"""
        return _guard(_vfs.fs_stat, virtual_path=_scoped(virtual_path))

    @m.tool()
    async def fs_create(virtual_path: str, content: str) -> dict:
        """治理写：创建对象（铸 id + policy 校验 + 单 repo 事务提交）。"""
        return _guard(_vfs.fs_create, virtual_path=_scoped(virtual_path),
                      content=content)

    @m.tool()
    async def fs_edit(virtual_path: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> dict:
        """治理写：精确子串替换（与 memory_edit 同一 policy → transaction 管线）。"""
        return _guard(_vfs.fs_edit, virtual_path=_scoped(virtual_path),
                      old_string=old_string, new_string=new_string,
                      replace_all=replace_all)

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

    def _tenant_cards(tenant: str) -> list[dict] | None:
        if tenant not in tenants:
            return None
        return store.list_cards(os.path.join(data_root, tenant))["cards"]

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
    data_root = os.environ.get("KATANA_MEMORY_DIR", "/data/memory")
    host = os.environ.get("KATANA_MEMORY_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_MEMORY_MCP_PORT", "5605"))
    uvicorn.run(build_app(data_root), host=host, port=port)


if __name__ == "__main__":
    main()
