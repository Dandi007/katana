"""katana-work-folder-mcp — work-folder 的 FastMCP server。

Tools:
  wf_search — 薄检索原语，复用 vault-search 栈，按 work_folder scope。
业务逻辑抽成纯函数便于单测；FastMCP tool 只做薄壳。
"""
import os
from fastmcp import FastMCP

from katana_kb_mcp_shared import config, vault_search

mcp = FastMCP(
    "katana-work-folder-mcp",
    instructions=(
        "work-folder 的 MCP 接口：跨 session 工作的创建/存档/恢复/检索；"
        "wf_search 做混合检索返回带路径候选。"
    ),
)

_scope: str | None = None
_wf_root: str | None = None


def compute_scope(work_folder_path: str, kb_root: str) -> str | None:
    """work_folder_path 相对 kb_root 的相对路径；相等或 '.' → None（整库，无 dir 过滤）。"""
    rel = os.path.relpath(work_folder_path, kb_root)
    return None if rel in (".", "") else rel


def configure(work_folder_path: str, kb_root: str) -> None:
    global _scope, _wf_root
    _scope = compute_scope(work_folder_path, kb_root)
    _wf_root = work_folder_path


def _do_search(query: str, top_k: int, scope: str | None) -> list[dict]:
    resp = vault_search.search(query, top_k=top_k, dir=scope)
    return [
        {"path": r.path, "score": r.score, "title": r.title, "snippet": r.snippet}
        for r in resp.results
    ]


@mcp.tool()
async def wf_search(query: str, top_k: int = 10) -> list[dict]:
    """对工作记录子树做混合检索（RRF：关键词+向量），返回带路径的候选。

    返回每条含 path/score/title/snippet。拿到 path 后可自行 read 全文 / grep / 顺 wikilink 深挖——
    本 tool 不替你嚼碎，保留你的自由探索。

    Args:
        query: 检索词或自然语言查询。
        top_k: 返回上限，默认 10。
    """
    return _do_search(query, top_k, _scope)


def main() -> None:
    wf_path = config.resolve("work_folder_path", default="docs/work-records", env_var="KATANA_WORK_FOLDER")
    kb = config.kb_root()
    configure(wf_path, kb)
    host = os.environ.get("KATANA_WORK_FOLDER_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WORK_FOLDER_MCP_PORT", "5602"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
