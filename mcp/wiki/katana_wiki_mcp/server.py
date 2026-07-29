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
from katana_kernel import (
    DomainPolicy,
    GovernedKernel,
    GovernedVFS,
    MutationBrokenError,
    ResourceIdLedger,
    TransactionManifest,
    head_sha,
)
from katana_wiki_mcp import enumerate as _enumerate
from katana_wiki_mcp import ingest as _ingest
from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp import lint as _lint
from katana_wiki_mcp import pages as _pages
from katana_wiki_mcp import query as _query
from katana_wiki_mcp.store import WikiStore, _wiki_policy
from katana_wiki_mcp.fs_tools import FSTools


def _server_mutation(call) -> dict:
    try:
        return call()
    except MutationBrokenError as exc:
        return exc.as_error()

mcp = FastMCP(
    "katana-wiki-mcp",
    instructions=(
        "Wiki 的 MCP 接口：知识检索/查询/入库。"
        "wiki_search 做混合检索并返回带路径的候选，agent 可据路径自行深挖。"
        "wiki_query 做 fat 检索：判重 + cold gap-log + 综合协议，是 query skill 的 server 侧。"
        "fs_* 工具提供 Full VFS 读写面：fs_resolve/fs_stat/fs_list/fs_glob/fs_read 做发现与读取；"
        "fs_create/fs_write/fs_edit/fs_copy/fs_rename/fs_delete 做受治理的 mutation；"
        "fs_batch 做单 repo all-or-nothing 批量事务。"
    ),
)

_scope: str | None = None
_wiki_root: str | None = None
_kernel: GovernedKernel | None = None
_store: WikiStore | None = None
_fs_tools: FSTools | None = None


def compute_scope(wiki_root: str, kb_root: str) -> str | None:
    """wiki_root 相对 kb_root 的相对路径；相等或 '.' → None（整库，无 dir 过滤）。"""
    rel = os.path.relpath(wiki_root, kb_root)
    return None if rel in (".", "") else rel


def _init_kernel(wiki_root: str) -> None:
    global _kernel, _store, _fs_tools
    if not os.path.isdir(wiki_root):
        return
    _kernel = GovernedKernel()
    vfs = GovernedVFS(wiki_root)
    ledger = ResourceIdLedger(
        os.path.join(wiki_root, ".katana", "tombstones.json"),
        prefix="w-",
    )
    manifest = TransactionManifest(os.path.join(wiki_root, ".katana", "manifests"))
    policy = _wiki_policy()
    _kernel.bind("wiki", policy, vfs, ledger, manifest, wiki_root)
    _store = WikiStore(_kernel)
    _fs_tools = FSTools(_kernel, wiki_root)


def configure(wiki_root: str, kb_root: str) -> None:
    global _scope, _wiki_root
    _scope = compute_scope(wiki_root, kb_root)
    _wiki_root = wiki_root
    _init_kernel(wiki_root)


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


def _governed_append_log(wiki_root: str, line: str) -> None:
    if _store is not None:
        _store.append_gap_log(line)
    else:
        _pages.append_log(wiki_root, line)


@mcp.tool()
async def wiki_query(question: str, top_k: int = 10) -> dict:
    """对 wiki 提问：server 判重检索 + 返回候选(带路径)与综合协议；空集走 cold 并记 gap log。

    返回 candidates 后，按 synthesis_contract 综合作答：每条 claim 带 citation 或标 [inference]；
    candidates 带 path，可自行 read 全文深挖。cold=True 表示 wiki 不覆盖，勿裸答冒充。

    Args:
        question: 问题文本。
        top_k: 候选上限，默认 10。
    """
    return _server_mutation(
        lambda: _query._do_query(
            question, _scope, _wiki_root or ".", top_k,
            search_fn=vault_search.search,
            log_fn=_governed_append_log,
            now_fn=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
    )


@mcp.tool()
async def wiki_ingest_plan(source_text: str) -> dict:
    """入库第一步：server orient 判重，并返回 proposal schema 与唯一 canonical base_sha。
    updates apply 必须传 expected_base_sha=plan.base_sha。Args: source_text 待入库内容(或其摘录)。"""
    return _ingest.plan(
        source_text,
        _scope,
        search_fn=vault_search.search,
        base_sha=head_sha(_wiki_root or "."),
    )


@mcp.tool()
async def wiki_ingest_apply(proposal: dict,
                            expected_base_sha: str | None = None) -> dict:
    """入库第二步：server 校验 create/update 意图及页面不变量；existing page 仅允许 updates 且必须保留 path/id，任何歧义均拒绝并零落盘。通过后经 kernel 治理链写页+自动反链+log+commit。
    Args: proposal 见 wiki_ingest_plan 返回的 proposal_schema。updates 非空时 expected_base_sha 必须使用 plan 返回值。返回 applied/rejected/commit。"""
    if _store is None:
        raise RuntimeError("wiki store not initialized; call configure() first")
    return _server_mutation(
        lambda: _store.ingest_apply(
            proposal, expected_base_sha=expected_base_sha,
        )
    )


@mcp.tool()
async def wiki_list_docs(zone: str | None = None) -> list[dict]:
    """枚举 wiki 全部可写文档（自动排除 raw/干扰目录），返回带路径的清单。

    返回每条含 path/类型/frontmatter/mtime/hash。拿到 path 后可自行 read/grep/顺 wikilink 深挖。
    Args: zone 可选，限定子目录前缀（如 "Zettelkasten/Index"）。
    """
    docs = _enumerate.enumerate_docs(_wiki_root or ".")
    if zone:
        docs = [d for d in docs if d["path"].startswith(zone.rstrip("/") + "/")]
    return docs


@mcp.tool()
async def wiki_lint_mechanical(path: str | None = None, zone: str | None = None) -> dict:
    """确定性机械体检：逐页不变量（缺 provenance/outlink/摘要/frontmatter）+ 跨页 orphan/broken_link。

    返回 {findings:[{path,code,detail}], skipped, scanned}。raw zone 自动豁免。
    Args: path 可选，限定单页逐页检查（跨页基线仍扫全 zone）；zone 可选，限定子目录前缀（如 "Zettelkasten"），跨页基线只在该 zone 内算。
    """
    return _lint.lint_mechanical(_wiki_root or ".", path, zone=zone)


# ── fs_* Full VFS tools ──────────────────────────────────────────────────

@mcp.tool()
async def fs_capabilities() -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_capabilities()


@mcp.tool()
async def fs_resolve(path_or_id: str) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_resolve(path_or_id)


@mcp.tool()
async def fs_stat(path: str) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_stat(path)


@mcp.tool()
async def fs_list(path: str = "") -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_list(path)


@mcp.tool()
async def fs_glob(pattern: str) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_glob(pattern)


@mcp.tool()
async def fs_read(path: str, offset: int | None = None,
                   limit: int | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_read(path, offset=offset, limit=limit)


@mcp.tool()
async def fs_create(path: str, content: str,
                    resource_id: str | None = None,
                    expected_base_sha: str | None = None,
                    idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_create(path, content, resource_id=resource_id,
                               expected_base_sha=expected_base_sha,
                               idempotency_key=idempotency_key)


@mcp.tool()
async def fs_write(path: str, content: str,
                   resource_id: str | None = None,
                   expected_base_sha: str | None = None,
                   expected_resource_revision: str | None = None,
                   idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_write(path, content, resource_id=resource_id,
                              expected_base_sha=expected_base_sha,
                              expected_resource_revision=expected_resource_revision,
                              idempotency_key=idempotency_key)


@mcp.tool()
async def fs_edit(path: str, old_string: str, new_string: str,
                  resource_id: str | None = None,
                  replace_all: bool = False,
                  expected_base_sha: str | None = None,
                  expected_resource_revision: str | None = None,
                  idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_edit(path, old_string, new_string,
                             resource_id=resource_id,
                             replace_all=replace_all,
                             expected_base_sha=expected_base_sha,
                             expected_resource_revision=expected_resource_revision,
                             idempotency_key=idempotency_key)


@mcp.tool()
async def fs_copy(source: str, dest: str,
                  resource_id: str | None = None,
                  expected_base_sha: str | None = None,
                  idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_copy(source, dest, resource_id=resource_id,
                             expected_base_sha=expected_base_sha,
                             idempotency_key=idempotency_key)


@mcp.tool()
async def fs_rename(source: str, dest: str,
                    resource_id: str | None = None,
                    expected_base_sha: str | None = None,
                    idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_rename(source, dest, resource_id=resource_id,
                               expected_base_sha=expected_base_sha,
                               idempotency_key=idempotency_key)


@mcp.tool()
async def fs_delete(path: str,
                    resource_id: str | None = None,
                    expected_base_sha: str | None = None,
                    idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_delete(path, resource_id=resource_id,
                               expected_base_sha=expected_base_sha,
                               idempotency_key=idempotency_key)


@mcp.tool()
async def fs_batch(operations: list[dict],
                   expected_base_commit: str | None = None,
                   idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("wiki fs_tools not initialized; call configure() first")
    return _fs_tools.fs_batch(operations, expected_base_commit=expected_base_commit,
                              idempotency_key=idempotency_key)


def build_remote_app(
    wiki_root: str,
    kb_root: str,
    credential_registry,
    *,
    rate_limiter=None,
    readiness_service=None,
    audit_logger=None,
    tenant_resolver=None,
):
    """Build the wiki app with remote auth middleware applied."""
    from katana_remote import create_remote_app, RateLimiter, ReadinessService, AuditLogger

    configure(wiki_root, kb_root)
    inner = mcp.http_app()
    rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
    readiness_service = readiness_service if readiness_service is not None else ReadinessService()
    audit_logger = audit_logger if audit_logger is not None else AuditLogger()

    return create_remote_app(
        inner,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain="wiki",
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
