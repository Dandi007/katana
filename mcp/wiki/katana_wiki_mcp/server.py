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
    # vault-search indexes several roots (wiki + work-records + vault) into one
    # index, and `dir` only scopes to a single prefix. When wiki_root == kb_root
    # the computed scope is None, so unfiltered results would include paths from
    # other domains that this server's VFS cannot even open (RESOURCE_NOT_FOUND).
    # Over-fetch, then keep only paths that actually exist in the wiki repo.
    fetch_k = top_k if scope else max(top_k * 4, 20)
    resp = vault_search.search(query, top_k=fetch_k, dir=scope)
    out: list[dict] = []
    for r in resp.results:
        if not scope and not _is_wiki_path(r.path):
            continue
        out.append(
            {"path": r.path, "score": r.score, "title": r.title, "snippet": r.snippet}
        )
        if len(out) >= top_k:
            break
    return out


def _is_wiki_path(path: str) -> bool:
    """Whether `path` (kb_root-relative) resolves to a real file in the wiki repo."""
    if not _wiki_root or not path:
        return False
    candidate = os.path.normpath(os.path.join(_wiki_root, path))
    root = os.path.normpath(_wiki_root)
    if not (candidate == root or candidate.startswith(root + os.sep)):
        return False
    return os.path.isfile(candidate)


def _wiki_scoped_search(query: str, *, top_k: int = 10, dir: str | None = None, **kwargs):
    """vault_search.search, with cross-domain results dropped.

    Injected into query/ingest so their dedup candidates obey the same wiki-only
    boundary as wiki_search — otherwise `cold` and create-vs-update decisions are
    made against pages from other domains.
    """
    fetch_k = top_k if dir else max(top_k * 4, 20)
    resp = vault_search.search(query, top_k=fetch_k, dir=dir, **kwargs)
    if not dir:
        resp.results = [r for r in resp.results if _is_wiki_path(r.path)]
    del resp.results[top_k:]
    return resp


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
            search_fn=_wiki_scoped_search,
            log_fn=_governed_append_log,
            now_fn=lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
    )


@mcp.tool()
async def wiki_report_gap(question: str, note: str | None = None) -> dict:
    """记录一次「wiki 未覆盖」——当 wiki_query 返回了候选但你自检后判定无一支撑本问题时调用。

    cold=False 只代表检索有返回。分数不能区分真命中与噪声（标题字面匹配得高分，
    自然语言提问命中真页面时分数与无关页同量级），所以「是否真覆盖」须由你阅读候选后判断；
    判为未覆盖时用本 tool 记 gap log，让知识盲区可见——否则 gap 只在结果集为空时才被记录。

    Args:
        question: 原始问题文本。
        note: 可选，补充说明（例如最接近的候选为何不够）。
    """
    if _store is None:
        raise RuntimeError("wiki store not initialized; call configure() first")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"## [{ts}] query | gap: {question}"
    if note:
        line += f" — {note}"
    return _server_mutation(lambda: _store.append_gap_log(line))


@mcp.tool()
async def wiki_ingest_plan(source_text: str) -> dict:
    """入库第一步：server orient 判重，并返回 proposal schema 与唯一 canonical base_sha。
    updates apply 必须传 expected_base_sha=plan.base_sha。Args: source_text 待入库内容(或其摘录)。"""
    return _ingest.plan(
        source_text,
        _scope,
        search_fn=_wiki_scoped_search,
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
    Args: zone 可选，限定子目录前缀（如 "DeepThought"）。
    """
    docs = _enumerate.enumerate_docs(_wiki_root or ".")
    if zone:
        docs = [d for d in docs if d["path"].startswith(zone.rstrip("/") + "/")]
    return docs


@mcp.tool()
async def wiki_lint_mechanical(path: str | None = None, zone: str | None = None,
                               offset: int = 0, limit: int | None = 200) -> dict:
    """确定性机械体检：逐页不变量（缺 provenance/outlink/摘要/frontmatter）+ 跨页 orphan/broken_link。

    返回 {findings, skipped, scanned, total_findings, by_code, affected_pages, offset, truncated}。
    raw zone 自动豁免；若 zone 落在排除区，skipped 会说明「未做检查」而非静默报干净。
    Args: path 可选，限定单页逐页检查（跨页基线仍扫全 zone）；zone 可选，限定子目录前缀（如 "DeepThought"），跨页基线只在该 zone 内算；
      offset/limit 对 findings 分页（默认每页 200；全库 findings 可达数千条，勿一次全取）。先看 by_code 汇总再按需翻页。
    """
    return _lint.lint_mechanical(_wiki_root or ".", path, zone=zone,
                                 offset=offset, limit=limit)


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
    host = os.environ.get("KATANA_WIKI_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WIKI_MCP_PORT", "5601"))
    creds = os.environ.get("KATANA_REMOTE_CREDENTIALS")
    if creds:
        import uvicorn

        from katana_remote.credstore import load_registry
        from katana_remote.runtime import audit_logger_from_env
        app = build_remote_app(wiki_root, kb, load_registry(creds),
                               audit_logger=audit_logger_from_env())
        uvicorn.run(app, host=host, port=port)
        return
    configure(wiki_root, kb)
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
