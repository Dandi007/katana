"""katana-work-folder-mcp — work-folder 的 FastMCP server。

Tools:
  wf_search  — 薄检索原语，复用 vault-search 栈，按 work_folder scope。
  wf_create  — 创建 work-folder（YYYY/MM/DD/<slug>/ 布局）。
  wf_list    — 列举未完成的 work-folder 候选。
  wf_save    — 存档 checkpoint（progress + context + resume guide）。
  wf_resume  — 恢复工作状态（环境验证 → MATCH/DRIFT/BROKEN）。
  wf_reindex — 扫全库 _brief.md 生成顶层 INDEX.md。

所有 mutating tool（wf_create/wf_save/wf_resume/wf_reindex）经 GovernedKernel.mutate
治理链：CAS → policy → VFS → ledger → manifest → git commit。
业务逻辑抽成纯函数便于单测；FastMCP tool 只做薄壳。
"""
import datetime
import os
import subprocess
from fastmcp import FastMCP

from katana_kb_mcp_shared import config, vault_search
from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    TransactionManifest,
)
from katana_work_folder_mcp import lifecycle as _lifecycle
from katana_work_folder_mcp import reindex as _reindex
from katana_work_folder_mcp.fs_tools import FSTools
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy

mcp = FastMCP(
    "katana-work-folder-mcp",
    instructions=(
        "work-folder 的 MCP 接口：跨 session 工作的创建/存档/恢复/检索；"
        "wf_search 做混合检索返回带路径候选。"
    ),
)

_scope: str | None = None
_wf_root: str | None = None
_kernel: GovernedKernel | None = None
_store: WorkFolderStore | None = None
_fs_tools: FSTools | None = None


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


def _rel_folder(folder: str) -> str:
    """将绝对路径转换为相对 _wf_root 的 VFS 路径。"""
    if _wf_root is None:
        return folder
    return os.path.relpath(os.path.realpath(folder), os.path.realpath(_wf_root))


def _abs_path(rel: str) -> str:
    """将 VFS 相对路径转换为绝对路径。"""
    if _wf_root is None:
        return rel
    return os.path.join(_wf_root, rel)


def _patch_store_result(result: dict, key: str) -> dict:
    """将 store 返回的 VFS-relative path/folder 转为绝对路径。"""
    if key in result and result[key] and not os.path.isabs(result[key]):
        result[key] = _abs_path(result[key])
    return result


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


def _ensure_git_repo(path: str) -> None:
    """确保 path 是一个 git 仓库；若不存在则初始化。"""
    git_dir = os.path.join(path, ".git")
    if not os.path.isdir(git_dir):
        subprocess.run(
            ["git", "init", "-q"],
            cwd=path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", path, "config", "user.email", "katana@localhost"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", path, "config", "user.name", "katana-work-folder"],
            check=True, capture_output=True,
        )


def configure(work_folder_path: str, kb_root: str) -> None:
    global _scope, _wf_root, _kernel, _store, _fs_tools
    _scope = compute_scope(work_folder_path, kb_root)
    _wf_root = work_folder_path

    if not os.path.isdir(work_folder_path):
        return

    _ensure_git_repo(work_folder_path)

    _kernel = GovernedKernel()
    vfs = GovernedVFS(work_folder_path)
    ledger = ResourceIdLedger(
        os.path.join(work_folder_path, ".katana", "tombstones.json"),
        prefix="wf-",
    )
    manifest = TransactionManifest(os.path.join(work_folder_path, ".katana", "manifests"))
    policy = _wf_policy()
    _kernel.bind("work-folder", policy, vfs, ledger, manifest, work_folder_path)
    _store = WorkFolderStore(_kernel)
    _fs_tools = FSTools(_kernel, work_folder_path)


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
# Fat lifecycle tools — 薄壳，路由到 store.*
# ---------------------------------------------------------------------------

@mcp.tool()
async def wf_create(topic: str,
                    expected_base_sha: str | None = None) -> dict:
    """按约定路径 <work_folder_root>/YYYY/MM/DD/<slug>/ 创建 work folder 并 seed progress/context。

    server 机械保证：路径布局、目录创建、初始文件 seed（已存在则不覆盖）。
    返回 path 供后续 wf_save/wf_resume 使用；drafting 字段含 Save 判断契约。

    Args:
        topic: 工作主题，用于生成 slug 和初始 goal 说明。
        expected_base_sha: 可选 CAS 预期 SHA，用于乐观并发控制。
    """
    if _store is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    result = _store.create(topic, now_fn=_now, expected_base_sha=expected_base_sha)
    return _patch_store_result(result, "path")


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
    expected_base_sha: str | None = None,
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
        expected_base_sha:      可选 CAS 预期 SHA，用于乐观并发控制。
    """
    if _store is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    abs_folder = _resolve_folder(folder)
    rel_folder = _rel_folder(abs_folder)
    result = _store.save(
        rel_folder,
        now_fn=_now,
        summary=summary,
        context_snapshot=context_snapshot,
        resume_fields=_safe_resume_fields(resume_fields),
        golden_order_additions=golden_order_additions,
        findings_addition=findings_addition,
        expected_base_sha=expected_base_sha,
    )
    return _patch_store_result(result, "folder")


@mcp.tool()
async def wf_resume(folder: str,
                    expected_base_sha: str | None = None) -> dict:
    """恢复工作状态：加载 artifact + server 实跑环境验证，返回 MATCH/DRIFT/BROKEN 结论。

    server 机械保证：路径验证、context.md 资源探针、blocked 不变量（BROKEN → blocked=True）。
    blocked=True（overall=BROKEN）时只输出阻塞报告、勿进入工作状态（按 contract 要求）。
    否则从 progress.md 的 Current/Next 接续，contract 指引你主动提出下一步行动。

    Args:
        folder: work-folder 路径（绝对或相对 work_folder_root）。
        expected_base_sha: 可选 CAS 预期 SHA，用于乐观并发控制。
    """
    if _store is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    abs_folder = _resolve_folder(folder)
    rel_folder = _rel_folder(abs_folder)
    result = _store.resume(rel_folder, now_fn=_now, expected_base_sha=expected_base_sha)
    return _patch_store_result(result, "folder")


@mcp.tool()
async def wf_reindex(dry_run: bool = False,
                     expected_base_sha: str | None = None) -> dict:
    """扫全 work_folder_root 下的 `_brief.md`，按 updated 倒序重生成顶层 INDEX.md。

    server 机械保证：递归扫描、parse、排序、写 INDEX.md（dry_run 时只返回 preview 不落盘）。
    wf_create/save/resume 只维护单个 folder 的 `_brief.md`；INDEX 是聚合视图，需要显式 reindex 刷新。

    Args:
        dry_run: True 时不写文件，返回 preview 字段含将生成的 INDEX 内容。
        expected_base_sha: 可选 CAS 预期 SHA，用于乐观并发控制。
    """
    if _store is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    result = _store.reindex(dry_run=dry_run, expected_base_sha=expected_base_sha)
    return _patch_store_result(result, "index_path")


def build_remote_app(
    work_folder_path: str,
    kb_root: str,
    credential_registry: "CredentialRegistry",
    *,
    rate_limiter=None,
    readiness_service=None,
    audit_logger=None,
    tenant_resolver=None,
):
    """Build the work-folder app with remote auth middleware applied."""
    from katana_remote import AuthMiddleware, RateLimiter, ReadinessService, AuditLogger

    configure(work_folder_path, kb_root)
    inner = mcp.http_app()
    rate_limiter = rate_limiter or RateLimiter()
    readiness_service = readiness_service or ReadinessService()
    audit_logger = audit_logger or AuditLogger()

    return AuthMiddleware(
        inner,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain="work-folder",
    )


def main() -> None:
    wf_path = config.resolve("work_folder_path", default="docs/work-records", env_var="KATANA_WORK_FOLDER")
    kb = config.kb_root()
    configure(wf_path, kb)
    host = os.environ.get("KATANA_WORK_FOLDER_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WORK_FOLDER_MCP_PORT", "5602"))
    mcp.run(transport="streamable-http", host=host, port=port)


# ---------------------------------------------------------------------------
# Full VFS (fs_*) tool wrappers — thin shells routing to FSTools
# ---------------------------------------------------------------------------

@mcp.tool()
async def fs_capabilities() -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_capabilities()


@mcp.tool()
async def fs_resolve(path_or_id: str) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_resolve(path_or_id)


@mcp.tool()
async def fs_stat(path: str) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_stat(path)


@mcp.tool()
async def fs_list(path: str = "") -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_list(path)


@mcp.tool()
async def fs_glob(pattern: str) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_glob(pattern)


@mcp.tool()
async def fs_read(path: str, offset: int | None = None, limit: int | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_read(path, offset=offset, limit=limit)


@mcp.tool()
async def fs_create(path: str, content: str,
                    resource_id: str | None = None,
                    expected_base_commit: str | None = None,
                    idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_create(path, content,
                                resource_id=resource_id,
                                expected_base_commit=expected_base_commit,
                                idempotency_key=idempotency_key)


@mcp.tool()
async def fs_write(path: str, content: str,
                   resource_id: str | None = None,
                   expected_base_commit: str | None = None,
                   expected_resource_revision: str | None = None,
                   idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_write(path, content,
                               resource_id=resource_id,
                               expected_base_commit=expected_base_commit,
                               expected_resource_revision=expected_resource_revision,
                               idempotency_key=idempotency_key)


@mcp.tool()
async def fs_edit(path: str, old_string: str, new_string: str,
                  resource_id: str | None = None,
                  replace_all: bool = False,
                  expected_base_commit: str | None = None,
                  expected_resource_revision: str | None = None,
                  idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_edit(path, old_string, new_string,
                              resource_id=resource_id,
                              replace_all=replace_all,
                              expected_base_commit=expected_base_commit,
                              expected_resource_revision=expected_resource_revision,
                              idempotency_key=idempotency_key)


@mcp.tool()
async def fs_copy(source: str, dest: str,
                  resource_id: str | None = None,
                  expected_base_commit: str | None = None,
                  idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_copy(source, dest,
                              resource_id=resource_id,
                              expected_base_commit=expected_base_commit,
                              idempotency_key=idempotency_key)


@mcp.tool()
async def fs_rename(source: str, dest: str,
                    resource_id: str | None = None,
                    expected_base_commit: str | None = None,
                    idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_rename(source, dest,
                                resource_id=resource_id,
                                expected_base_commit=expected_base_commit,
                                idempotency_key=idempotency_key)


@mcp.tool()
async def fs_delete(path: str,
                    resource_id: str | None = None,
                    expected_base_commit: str | None = None,
                    idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_delete(path,
                                resource_id=resource_id,
                                expected_base_commit=expected_base_commit,
                                idempotency_key=idempotency_key)


@mcp.tool()
async def fs_batch(operations: list[dict],
                   expected_base_commit: str | None = None,
                   idempotency_key: str | None = None) -> dict:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools.fs_batch(operations,
                               expected_base_commit=expected_base_commit,
                               idempotency_key=idempotency_key)


if __name__ == "__main__":
    main()