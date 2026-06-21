"""katana-wiki-mcp — wiki 的 FastMCP server。

第一个 tool：wiki_search（薄检索原语，复用 vault-search 栈，按 wiki_root scope）。
业务逻辑抽成纯函数（compute_scope / _do_search）便于单测；FastMCP tool 只做薄壳。
"""
import os
from fastmcp import FastMCP

from katana_kb_mcp_shared import config, vault_search

mcp = FastMCP(
    "katana-wiki-mcp",
    instructions=(
        "Wiki 的 MCP 接口：知识检索/查询/入库。wiki_search 做混合检索并返回带路径的候选，"
        "agent 可据路径自行深挖（read/grep/follow link）。"
    ),
)

_scope: str | None = None


def compute_scope(wiki_root: str, kb_root: str) -> str | None:
    """wiki_root 相对 kb_root 的相对路径；相等或 '.' → None（整库，无 dir 过滤）。"""
    rel = os.path.relpath(wiki_root, kb_root)
    return None if rel in (".", "") else rel


def configure(wiki_root: str, kb_root: str) -> None:
    global _scope
    _scope = compute_scope(wiki_root, kb_root)


def _do_search(query: str, top_k: int, scope: str | None) -> list[dict]:
    resp = vault_search.search(query, top_k=top_k, dir=scope)
    return [
        {"path": r.path, "score": r.score, "title": r.title, "snippet": r.snippet}
        for r in resp.results
    ]


@mcp.tool()
async def wiki_search(query: str, top_k: int = 10) -> list[dict]:
    """对 wiki 做混合检索（RRF：关键词+向量），返回带路径的候选。

    返回每条含 path/score/title/snippet。拿到 path 后可自行 read 全文 / grep / 顺 wikilink 深挖——
    本 tool 不替你嚼碎，保留你的自由探索。

    Args:
        query: 检索词或自然语言查询。
        top_k: 返回上限，默认 10。
    """
    return _do_search(query, top_k, _scope)


def main() -> None:
    wiki_root = config.resolve("wiki_root", default=".", env_var="KATANA_WIKI_ROOT")
    kb = config.kb_root()
    configure(wiki_root, kb)
    host = os.environ.get("KATANA_WIKI_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WIKI_MCP_PORT", "5601"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
