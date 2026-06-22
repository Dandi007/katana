"""katana-wiki-mcp — wiki 的 FastMCP server。

Tools:
  wiki_search — 薄检索原语，复用 vault-search 栈，按 wiki_root scope。
  wiki_query  — fat tool：判重检索 + cold gap-log + 返回综合协议文本。
业务逻辑抽成纯函数便于单测；FastMCP tool 只做薄壳。
"""
import datetime
import os
from fastmcp import FastMCP

from katana_kb_mcp_shared import config, vault_search
from katana_wiki_mcp import ingest as _ingest
from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp import pages as _pages
from katana_wiki_mcp import query as _query

mcp = FastMCP(
    "katana-wiki-mcp",
    instructions=(
        "Wiki 的 MCP 接口：知识检索/查询/入库。"
        "wiki_search 做混合检索并返回带路径的候选，agent 可据路径自行深挖。"
        "wiki_query 做 fat 检索：判重 + cold gap-log + 综合协议，是 query skill 的 server 侧。"
    ),
)

_scope: str | None = None
_wiki_root: str | None = None


def compute_scope(wiki_root: str, kb_root: str) -> str | None:
    """wiki_root 相对 kb_root 的相对路径；相等或 '.' → None（整库，无 dir 过滤）。"""
    rel = os.path.relpath(wiki_root, kb_root)
    return None if rel in (".", "") else rel


def configure(wiki_root: str, kb_root: str) -> None:
    global _scope, _wiki_root
    _scope = compute_scope(wiki_root, kb_root)
    _wiki_root = wiki_root


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


@mcp.tool()
async def wiki_query(question: str, top_k: int = 10) -> dict:
    """对 wiki 提问：server 判重检索 + 返回候选(带路径)与综合协议；空集走 cold 并记 gap log。

    返回 candidates 后，按 synthesis_contract 综合作答：每条 claim 带 citation 或标 [inference]；
    candidates 带 path，可自行 read 全文深挖。cold=True 表示 wiki 不覆盖，勿裸答冒充。

    Args:
        question: 问题文本。
        top_k: 候选上限，默认 10。
    """
    return _query._do_query(
        question, _scope, _wiki_root or ".", top_k,
        search_fn=vault_search.search,
        log_fn=_pages.append_log,
        now_fn=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


@mcp.tool()
async def wiki_ingest_plan(source_text: str) -> dict:
    """入库第一步：server orient 判重 + 返回判断指令(create-vs-update/resist-table/拆单元)与 proposal schema。
    据此造 proposal 交给 wiki_ingest_apply。Args: source_text 待入库内容(或其摘录)。"""
    return _ingest.plan(source_text, _scope, search_fn=vault_search.search)


@mcp.tool()
async def wiki_ingest_apply(proposal: dict) -> dict:
    """入库第二步：server 校验不变量(缺 provenance/outlink/frontmatter 必拒,零落盘)→ 通过则写页+自动反链+log+commit。
    Args: proposal 见 wiki_ingest_plan 返回的 proposal_schema。返回 applied/rejected/commit。"""
    return _ingest.apply(
        proposal, _wiki_root or ".",
        validate_fn=_inv.validate_page,
        write_fn=_pages.write_page, backlink_fn=_pages.ensure_backlink,
        log_fn=_pages.append_log, commit_fn=_pages.git_commit,
    )


def main() -> None:
    wiki_root = config.resolve("wiki_root", default=".", env_var="KATANA_WIKI_ROOT")
    kb = config.kb_root()
    configure(wiki_root, kb)
    host = os.environ.get("KATANA_WIKI_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WIKI_MCP_PORT", "5601"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
