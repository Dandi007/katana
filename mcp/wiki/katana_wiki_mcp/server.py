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
from katana_kb_mcp_shared.kernel import (
    Catalog, GovernedVFS, KernelError, TransactionEngine,
)
from katana_kb_mcp_shared.kernel.policy import AppComposition
from katana_kb_mcp_shared.kernel import paths as paths_mod
from katana_wiki_mcp import enumerate as _enumerate
from katana_wiki_mcp import ingest as _ingest
from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp import lint as _lint
from katana_wiki_mcp import pages as _pages
from katana_wiki_mcp import query as _query
from katana_wiki_mcp.policy import ID_PREFIX, WikiPolicy

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
_vfs: GovernedVFS | None = None


def compute_scope(wiki_root: str, kb_root: str) -> str | None:
    """wiki_root 相对 kb_root 的相对路径；相等或 '.' → None（整库，无 dir 过滤）。"""
    rel = os.path.relpath(wiki_root, kb_root)
    return None if rel in (".", "") else rel


def configure(wiki_root: str, kb_root: str) -> None:
    global _scope, _wiki_root, _vfs
    _scope = compute_scope(wiki_root, kb_root)
    _wiki_root = wiki_root
    # Governed Full VFS composition root (design §4.2/§5.2): fs_* shares the same
    # policy → transaction pipeline as the domain tools; no raw bypass (INV-10).
    composition = AppComposition(WikiPolicy())
    engine = TransactionEngine(wiki_root, domain="wiki",
                               policy_version=composition.policy.policy_version)
    catalog = Catalog(wiki_root, id_prefix=ID_PREFIX)
    _vfs = GovernedVFS(engine, catalog, composition.policy)
    if os.path.isdir(wiki_root):
        engine.reconcile()
        remote = os.environ.get("KATANA_WIKI_REMOTE")
        try:
            engine.drain_remote_once(remote)
        except KernelError:
            pass


def _require_vfs() -> GovernedVFS:
    if _vfs is None:
        raise ValueError("wiki VFS not configured; call configure() first")
    return _vfs


def _staged_commit(staging, message: str, paths: list[str]) -> str:
    """Publish pages/backlinks/log projected into writer-private staging.

    ``wiki_ingest_apply`` runs its projection against a private copy of HEAD
    (never the canonical working tree; operator P0 #2), then hands the touched
    relative paths here. They are compiled into ONE MutationBatch and published
    through the SAME WikiPolicy + TransactionEngine as fs_* (design §4.4,
    INV-5). Policy rejection leaves zero client-visible effect (only staging was
    written). Returns the new commit SHA (or current head on a no-op).
    """
    vfs = _require_vfs()
    rels = []
    for p in paths:
        rel = paths_mod.confine(p)
        ap = paths_mod.confined_join(staging.root, rel)
        if os.path.isfile(ap) and not os.path.islink(ap) and rel not in rels:
            rels.append(rel)
    res = vfs.commit_staged(staging, message=message, writes=rels)
    return res.commit_sha or (vfs.engine.repo.head() or "")


def _append_gap_event(wiki_root: str, line: str) -> None:
    """Append a cold-query coverage gap to the reserved operational sink.

    This is an operational event, not canonical content: it lives under the
    git-excluded ``.kb/query-gaps.log`` so it never enters a MutationBatch,
    never dirties the working tree, and never requires a commit (operator P0
    #3). Governed ingest (wiki_ingest_apply) is the only path that mutates
    canonical wiki content.
    """
    import os as _os
    # Register the operational exclude so the sink never appears as untracked.
    if _vfs is not None:
        try:
            _vfs.engine.repo._ensure_operational_excludes()
        except Exception:
            pass
    kb = _os.path.join(wiki_root, ".kb")
    _os.makedirs(kb, exist_ok=True)
    entry = line if line.endswith("\n") else line + "\n"
    with open(_os.path.join(kb, "query-gaps.log"), "a", encoding="utf-8") as f:
        f.write(entry)


def _guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except KernelError as e:
        raise ValueError(e.to_envelope()) from e


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
    # Cold-query gap logging is a NON-canonical operational event (operator
    # P0 #3): it records a coverage gap for later governed ingest but must not
    # mutate canonical content or dirty the working tree. It is appended to the
    # git-excluded reserved operational sink, so a cold query leaves
    # `git status --porcelain` clean and bypasses no governance.
    return _query._do_query(
        question, _scope, _wiki_root or ".", top_k,
        search_fn=vault_search.search,
        log_fn=_append_gap_event,
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
    vfs = _require_vfs()
    # Project the whole ingest (page write + backlink + log) into writer-private
    # staging, then publish as ONE governed transaction (operator P0 #2). The
    # canonical working tree is only touched by the engine's post-publish
    # materialize, so a rejected ingest leaves zero visible effect.
    with vfs.staging() as stg:
        return _ingest.apply(
            proposal, stg.root,
            validate_fn=_inv.validate_page,
            write_fn=_pages.write_page, backlink_fn=_pages.ensure_backlink,
            log_fn=_pages.append_log,
            commit_fn=lambda root, msg, paths: _staged_commit(stg, msg, paths),
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



# ── Governed Full VFS façade (fs_*) — design §5.2 ────────────────────────────

@mcp.tool()
async def fs_read(virtual_path: str, offset: int | None = None,
                  limit: int | None = None) -> dict:
    """治理 VFS 读（canonical tree，cat -n），path 相对 wiki_root。"""
    return _guard(_require_vfs().fs_read, virtual_path=virtual_path,
                  offset=offset, limit=limit)


@mcp.tool()
async def fs_list(virtual_path: str = "") -> list[dict]:
    """列出 wiki 子树节点（reserved namespace 隐藏）。"""
    return _guard(_require_vfs().fs_list, virtual_path)


@mcp.tool()
async def fs_stat(virtual_path: str) -> dict:
    """节点统一 descriptor（id/path/hash/revision/snapshot commit）。"""
    return _guard(_require_vfs().fs_stat, virtual_path=virtual_path)


@mcp.tool()
async def fs_create(virtual_path: str, content: str) -> dict:
    """治理写：创建对象（铸 id + WIKI schema 校验 + 单 repo 事务提交）。"""
    return _guard(_require_vfs().fs_create, virtual_path=virtual_path,
                  content=content)


@mcp.tool()
async def fs_edit(virtual_path: str, old_string: str, new_string: str,
                  replace_all: bool = False) -> dict:
    """治理写：精确子串替换（与 domain tools 同一 policy → transaction 管线）。"""
    return _guard(_require_vfs().fs_edit, virtual_path=virtual_path,
                  old_string=old_string, new_string=new_string,
                  replace_all=replace_all)


@mcp.tool()
async def fs_write(virtual_path: str, content: str,
                   expected_base_commit: str | None = None) -> dict:
    """治理写：整文件覆盖（不隐式创建，带 CAS）。"""
    return _guard(_require_vfs().fs_write, virtual_path=virtual_path,
                  content=content, expected_base_commit=expected_base_commit)


@mcp.tool()
async def fs_mkdir(virtual_path: str) -> dict:
    """治理写：创建目录（.gitkeep 落盘）。"""
    return _guard(_require_vfs().fs_mkdir, virtual_path=virtual_path)


@mcp.tool()
async def fs_copy(virtual_path: str, new_path: str) -> dict:
    """治理写：复制对象（铸新 id）。"""
    return _guard(_require_vfs().fs_copy, virtual_path=virtual_path,
                  new_path=new_path)


@mcp.tool()
async def fs_rename(virtual_path: str, new_path: str) -> dict:
    """治理写：重命名/移动（保 id；catalog 同批更新）。"""
    return _guard(_require_vfs().fs_rename, virtual_path=virtual_path,
                  new_path=new_path)


@mcp.tool()
async def fs_delete(virtual_path: str) -> dict:
    """治理写：删除（留 tombstone，id 不复用）。"""
    return _guard(_require_vfs().fs_delete, virtual_path=virtual_path)


@mcp.tool()
async def fs_batch(changes: list[dict],
                   expected_base_commit: str | None = None) -> dict:
    """治理写：单 repo all-or-nothing 批量事务（design §5.2 fs_batch）。"""
    return _guard(_require_vfs().fs_batch, changes,
                  expected_base_commit=expected_base_commit)


@mcp.tool()
async def fs_resolve(virtual_path: str) -> dict:
    """path → resource_id/exists 解析（不落盘）。"""
    return _guard(_require_vfs().fs_resolve, virtual_path)


@mcp.tool()
async def fs_glob(pattern: str) -> list[dict]:
    """glob 枚举（reserved namespace 隐藏）。"""
    return _guard(_require_vfs().fs_glob, pattern)


@mcp.tool()
async def fs_changes(since: str | None = None) -> dict:
    """自某 snapshot commit 起的已提交变更。"""
    return _guard(_require_vfs().fs_changes, since=since)


@mcp.tool()
async def fs_capabilities() -> dict:
    """协议版本 + 支持的 operations + 特性发现（design §5.1）。"""
    return _guard(_require_vfs().fs_capabilities)


@mcp.tool()
async def fs_status() -> dict:
    """异步 push/projection freshness + checkpoint（design §6.5-6.8）。"""
    return _guard(_require_vfs().fs_status)


def main() -> None:
    wiki_root = config.resolve("wiki_root", default=".", env_var="KATANA_WIKI_ROOT")
    kb = config.kb_root()
    configure(wiki_root, kb)
    host = os.environ.get("KATANA_WIKI_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WIKI_MCP_PORT", "5601"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
