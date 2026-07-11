"""katana-work-folder-mcp — work-folder 的 FastMCP server。

Tools:
  wf_search  — 薄检索原语，复用 vault-search 栈，按 work_folder scope。
  wf_create  — 创建 work-folder（YYYY/MM/DD/<slug>/ 布局）。
  wf_list    — 列举未完成的 work-folder 候选。
  wf_save    — 存档 checkpoint（progress + context + resume guide）。
  wf_resume  — 恢复工作状态（环境验证 → MATCH/DRIFT/BROKEN）。
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
from katana_work_folder_mcp import lifecycle as _lifecycle
from katana_work_folder_mcp import reindex as _reindex
from katana_work_folder_mcp.policy import ID_PREFIX, WorkFolderPolicy

mcp = FastMCP(
    "katana-work-folder-mcp",
    instructions=(
        "work-folder 的 MCP 接口：跨 session 工作的创建/存档/恢复/检索；"
        "wf_search 做混合检索返回带路径候选。"
    ),
)

_scope: str | None = None
_wf_root: str | None = None
_vfs: GovernedVFS | None = None


# ---------------------------------------------------------------------------
# 模块级辅助（边界硬化层）
# ---------------------------------------------------------------------------

def _now() -> datetime.datetime:
    """返回当前时间（注入点，便于测试替换）。"""
    return datetime.datetime.now()


def _resolve_folder(folder: str) -> str:
    """将 folder 参数解析为绝对路径。

    - 已是绝对路径：原样返回（server 不篡改模型给出的绝对路径）。
    - 相对路径：拼接到 _wf_root（或 '.' fallback）。
    """
    if os.path.isabs(folder):
        return folder
    return os.path.join(_wf_root or ".", folder)


# resume_fields 白名单：与 artifacts.gen_resume_guide 关键字参数一一对应
_RESUME_FIELD_KEYS = {"goal", "phase", "status", "wf_abs", "key_context", "decisions", "issues", "lessons", "now"}


def _safe_resume_fields(d: dict | None) -> dict | None:
    """过滤 resume_fields，只保留白名单键，防止模型传入脏 key 崩溃 gen_resume_guide。

    None 或空 dict → 返回 None（让 lifecycle 侧走自动推导分支）。
    """
    if not d:
        return None
    return {k: v for k, v in d.items() if k in _RESUME_FIELD_KEYS}


def compute_scope(work_folder_path: str, kb_root: str) -> str | None:
    """work_folder_path 相对 kb_root 的相对路径；相等或 '.' → None（整库，无 dir 过滤）。"""
    rel = os.path.relpath(work_folder_path, kb_root)
    return None if rel in (".", "") else rel


def configure(work_folder_path: str, kb_root: str) -> None:
    global _scope, _wf_root, _vfs
    _scope = compute_scope(work_folder_path, kb_root)
    _wf_root = work_folder_path
    # Governed Full VFS composition root (design §4.2/§5.2): fs_* shares the same
    # policy → transaction pipeline as the domain tools; no raw bypass (INV-10).
    composition = AppComposition(WorkFolderPolicy())
    engine = TransactionEngine(work_folder_path, domain="work-folder",
                               policy_version=composition.policy.policy_version)
    catalog = Catalog(work_folder_path, id_prefix=ID_PREFIX)
    _vfs = GovernedVFS(engine, catalog, composition.policy)
    if os.path.isdir(work_folder_path):
        engine.reconcile()
        remote = os.environ.get("KATANA_WORK_FOLDER_REMOTE")
        try:
            engine.drain_remote_once(remote)
        except KernelError:
            pass


def _require_vfs() -> GovernedVFS:
    if _vfs is None:
        raise ValueError("work-folder VFS not configured; call configure() first")
    return _vfs


def _guard(fn, *a, **k):
    try:
        return fn(*a, **k)
    except KernelError as e:
        raise ValueError(e.to_envelope()) from e


def _rel_to_root_of(root: str, path: str) -> str:
    """Confined path relative to a given root (accepts absolute or relative)."""
    root_abs = os.path.abspath(root)
    if os.path.isabs(path):
        real = os.path.realpath(path)
        root_real = os.path.realpath(root_abs)
        if real != root_real and not real.startswith(root_real + os.sep):
            raise KernelError("INVALID_PATH", "path escapes work-folder root",
                              virtual_path=path)
        rel = os.path.relpath(real, root_real).replace(os.sep, "/")
    else:
        rel = path.replace(os.sep, "/")
    return paths_mod.confine(rel)


def _rel_to_root(path: str) -> str:
    """Confined path relative to the configured wf root."""
    return _rel_to_root_of(os.path.abspath(_wf_root or "."), path)


def _govern_staged(stg, message: str, staged_abs, extra_rel=None):
    """Publish WF artifacts projected into writer-private staging.

    The lifecycle helpers (do_create/do_save/do_resume) and reindex run their
    domain projection against a private copy of HEAD (never the canonical
    working tree; operator P0 #2), then hand the resulting staging-absolute
    paths here. They are compiled into ONE MutationBatch and published through
    the SAME WorkFolderPolicy + TransactionEngine as fs_* (design §4.4, INV-5).
    A rejected mutation leaves zero client-visible effect. Only real files under
    the staging root are published, so routing unit tests are unaffected.
    """
    if _vfs is None:
        return None
    root = os.path.abspath(stg.root)
    rels = [paths_mod.confine(r) for r in (extra_rel or [])]
    for ap in (staged_abs or []):
        ap = os.path.abspath(ap)
        real_ap = os.path.realpath(ap)
        real_root = os.path.realpath(root)
        if real_ap != real_root and not real_ap.startswith(real_root + os.sep):
            raise KernelError("INVALID_PATH", "staged path escapes root",
                              virtual_path=ap)
        if not os.path.isfile(ap) or os.path.islink(ap):
            continue
        rel = paths_mod.confine(os.path.relpath(ap, root).replace(os.sep, "/"))
        if rel not in rels:
            rels.append(rel)
    if not rels:
        return None
    return _guard(_vfs.commit_staged, stg, message=message, writes=rels)


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


# ---------------------------------------------------------------------------
# Fat lifecycle tools — 薄壳，路由到 lifecycle.*
# ---------------------------------------------------------------------------

@mcp.tool()
async def wf_create(topic: str) -> dict:
    """按约定路径 <work_folder_root>/YYYY/MM/DD/<slug>/ 创建 work folder 并 seed progress/context。

    server 机械保证：路径布局、目录创建、初始文件 seed（已存在则不覆盖）。
    返回 path 供后续 wf_save/wf_resume 使用；drafting 字段含 Save 判断契约。

    Args:
        topic: 工作主题，用于生成 slug 和初始 goal 说明。
    """
    if _vfs is None:  # routing-only mode (no governed repo configured)
        return _lifecycle.do_create(_wf_root or ".", topic, now_fn=_now)
    with _vfs.staging() as stg:
        result = _lifecycle.do_create(stg.root, topic, now_fn=_now)
        folder = result.get("path")
        staged_abs = ([os.path.join(folder, n)
                       for n in (result.get("seeded") or [])] if folder else None)
        rel = _rel_to_root_of(stg.root, folder) if folder else ""
        _govern_staged(stg, f"feat(work-folder): create {rel}", staged_abs)
    if result.get("path"):
        result["path"] = os.path.join(os.path.abspath(_wf_root or "."), rel)
    return result


@mcp.tool()
async def wf_list(limit: int = 10) -> dict:
    """倒序列出未完成（status≠completed）的 work folder 候选（递归扫 YYYY/MM/DD 布局）。

    server 机械保证：扫描路径布局、过滤 completed、按 mtime 降序。
    返回 candidates 列表，每条含 path/status/mtime——你据此选择 resume 目标。

    Args:
        limit: 返回上限，默认 10。
    """
    return _lifecycle.do_list(_wf_root or ".", limit=limit)


@mcp.tool()
async def wf_save(
    folder: str,
    summary: str = "checkpoint",
    context_snapshot: str | None = None,
    resume_fields: dict | None = None,
    golden_order_additions: str | None = None,
    findings_addition: str | None = None,
) -> dict:
    """存档 checkpoint：追加 progress changelog、覆盖 context 快照（若给）、重生成 CLAUDE.md/AGENTS.md。

    server 机械保证：changelog 时间戳、resume guide 文件写入、文件幂等种子。
    golden-order/findings 内容由你起草（判断半），按返回的 contract 维护。
    resume_fields 的键经白名单过滤，防止脏键崩溃 gen_resume_guide。

    Args:
        folder:                 work-folder 路径（绝对或相对 work_folder_root）。
        summary:                changelog 摘要说明（默认 "checkpoint"）。
        context_snapshot:       若给定，覆盖写入 context.md（完整快照，非追加）。
        resume_fields:          传给 gen_resume_guide 的字段 dict；None 时从 progress.md 自动推导。
        golden_order_additions: 追加到 golden-order.md 的文字块（仅追加，不覆盖）。
        findings_addition:      追加到 findings.md 的文字块（仅追加，不覆盖）。
    """
    if _vfs is None:  # routing-only mode
        return _lifecycle.do_save(
            _resolve_folder(folder), now_fn=_now, summary=summary,
            context_snapshot=context_snapshot,
            resume_fields=_safe_resume_fields(resume_fields),
            golden_order_additions=golden_order_additions,
            findings_addition=findings_addition)
    rel_folder = _rel_to_root(_resolve_folder(folder))
    with _vfs.staging() as stg:
        result = _lifecycle.do_save(
            os.path.join(stg.root, rel_folder),
            now_fn=_now,
            summary=summary,
            context_snapshot=context_snapshot,
            resume_fields=_safe_resume_fields(resume_fields),
            golden_order_additions=golden_order_additions,
            findings_addition=findings_addition,
        )
        staged_abs = [os.path.join(stg.root, rel_folder, n)
                      for n in (result.get("written") or [])]
        _govern_staged(stg, f"chore(work-folder): save {rel_folder}", staged_abs)
    result["folder"] = os.path.join(os.path.abspath(_wf_root or "."), rel_folder)
    return result


@mcp.tool()
async def wf_resume(folder: str) -> dict:
    """恢复工作状态：加载 artifact + server 实跑环境验证，返回 MATCH/DRIFT/BROKEN 结论。

    server 机械保证：路径验证、context.md 资源探针、blocked 不变量（BROKEN → blocked=True）。
    blocked=True（overall=BROKEN）时只输出阻塞报告、勿进入工作状态（按 contract 要求）。
    否则从 progress.md 的 Current/Next 接续，contract 指引你主动提出下一步行动。

    Args:
        folder: work-folder 路径（绝对或相对 work_folder_root）。
    """
    if _vfs is None:  # routing-only mode
        return _lifecycle.do_resume(_resolve_folder(folder), now_fn=_now)
    rel_folder = _rel_to_root(_resolve_folder(folder))
    with _vfs.staging() as stg:
        result = _lifecycle.do_resume(os.path.join(stg.root, rel_folder),
                                      now_fn=_now)
        if result.get("ok"):
            _govern_staged(
                stg, f"chore(work-folder): resume {rel_folder}",
                [os.path.join(stg.root, rel_folder, n)
                 for n in ("progress.md", "_brief.md")])
    if result.get("folder"):
        result["folder"] = os.path.join(os.path.abspath(_wf_root or "."),
                                        rel_folder)
    return result


@mcp.tool()
async def wf_reindex(dry_run: bool = False) -> dict:
    """扫全 work_folder_root 下的 `_brief.md`，按 updated 倒序重生成顶层 INDEX.md。

    server 机械保证：递归扫描、parse、排序、写 INDEX.md（dry_run 时只返回 preview 不落盘）。
    wf_create/save/resume 只维护单个 folder 的 `_brief.md`；INDEX 是聚合视图，需要显式 reindex 刷新。

    Args:
        dry_run: True 时不写文件，返回 preview 字段含将生成的 INDEX 内容。
    """
    if dry_run or _vfs is None:
        return _reindex.reindex(_wf_root or ".", dry_run=dry_run)
    with _vfs.staging() as stg:
        result = _reindex.reindex(stg.root, dry_run=False)
        if result.get("index_path"):
            _govern_staged(stg, "chore(work-folder): reindex INDEX.md", None,
                           extra_rel=[_rel_to_root_of(stg.root,
                                                      result["index_path"])])
    if result.get("index_path"):
        result["index_path"] = os.path.join(os.path.abspath(_wf_root or "."),
                                             "INDEX.md")
    return result



# ── Governed Full VFS façade (fs_*) — design §5.2 ────────────────────────────

@mcp.tool()
async def fs_read(virtual_path: str, offset: int | None = None,
                  limit: int | None = None) -> dict:
    """治理 VFS 读（canonical tree，cat -n），path 相对 work_folder_root。"""
    return _guard(_require_vfs().fs_read, virtual_path=virtual_path,
                  offset=offset, limit=limit)


@mcp.tool()
async def fs_list(virtual_path: str = "") -> list[dict]:
    """列出 work-folder 子树节点（reserved namespace 隐藏）。"""
    return _guard(_require_vfs().fs_list, virtual_path)


@mcp.tool()
async def fs_stat(virtual_path: str) -> dict:
    """节点统一 descriptor（id/path/hash/revision/snapshot commit）。"""
    return _guard(_require_vfs().fs_stat, virtual_path=virtual_path)


@mcp.tool()
async def fs_create(virtual_path: str, content: str) -> dict:
    """治理写：创建对象（铸 id + _brief schema 校验 + 单 repo 事务提交）。"""
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
    wf_path = config.resolve("work_folder_path", default="docs/work-records", env_var="KATANA_WORK_FOLDER")
    kb = config.kb_root()
    configure(wf_path, kb)
    host = os.environ.get("KATANA_WORK_FOLDER_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WORK_FOLDER_MCP_PORT", "5602"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
