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
from katana_work_folder_mcp import lifecycle as _lifecycle
from katana_work_folder_mcp import reindex as _reindex

mcp = FastMCP(
    "katana-work-folder-mcp",
    instructions=(
        "work-folder 的 MCP 接口：跨 session 工作的创建/存档/恢复/检索；"
        "wf_search 做混合检索返回带路径候选。"
    ),
)

_scope: str | None = None
_wf_root: str | None = None


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
    return _lifecycle.do_create(_wf_root or ".", topic, now_fn=_now)


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
    return _lifecycle.do_save(
        _resolve_folder(folder),
        now_fn=_now,
        summary=summary,
        context_snapshot=context_snapshot,
        resume_fields=_safe_resume_fields(resume_fields),
        golden_order_additions=golden_order_additions,
        findings_addition=findings_addition,
    )


@mcp.tool()
async def wf_resume(folder: str) -> dict:
    """恢复工作状态：加载 artifact + server 实跑环境验证，返回 MATCH/DRIFT/BROKEN 结论。

    server 机械保证：路径验证、context.md 资源探针、blocked 不变量（BROKEN → blocked=True）。
    blocked=True（overall=BROKEN）时只输出阻塞报告、勿进入工作状态（按 contract 要求）。
    否则从 progress.md 的 Current/Next 接续，contract 指引你主动提出下一步行动。

    Args:
        folder: work-folder 路径（绝对或相对 work_folder_root）。
    """
    return _lifecycle.do_resume(_resolve_folder(folder), now_fn=_now)


@mcp.tool()
async def wf_reindex(dry_run: bool = False) -> dict:
    """扫全 work_folder_root 下的 `_brief.md`，按 updated 倒序重生成顶层 INDEX.md。

    server 机械保证：递归扫描、parse、排序、写 INDEX.md（dry_run 时只返回 preview 不落盘）。
    wf_create/save/resume 只维护单个 folder 的 `_brief.md`；INDEX 是聚合视图，需要显式 reindex 刷新。

    Args:
        dry_run: True 时不写文件，返回 preview 字段含将生成的 INDEX 内容。
    """
    return _reindex.reindex(_wf_root or ".", dry_run=dry_run)


def main() -> None:
    wf_path = config.resolve("work_folder_path", default="docs/work-records", env_var="KATANA_WORK_FOLDER")
    kb = config.kb_root()
    configure(wf_path, kb)
    host = os.environ.get("KATANA_WORK_FOLDER_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WORK_FOLDER_MCP_PORT", "5602"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
