"""lifecycle.py — work-folder MCP 的业务逻辑核心层（fat-tool 状态机）。

职责：
  - create / save / resume / list 四个操作的完整逻辑
  - 消费 verify.py（环境验证）和 artifacts.py（artifact I/O）
  - 核心不变量：resume 遇 BROKEN → blocked=True（服务端强制停止，比 skill 更强）

约束：
  - 仅依赖标准库 + verify + artifacts，不引入 server/config/LLM
  - 中文注释，术语保持英文
  - 不执行 git commit，不创建 spec.md / plan.md
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict
from pathlib import Path

from katana_work_folder_mcp import artifacts as _art
from katana_work_folder_mcp import verify as _ver

# ---------------------------------------------------------------------------
# 协议常量（返回给模型的"判断半"）
# ---------------------------------------------------------------------------

SAVE_CONTRACT: str = (
    "【Save 判断契约】"
    "① golden-order：宁多勿漏，只补本次 session 中尚未落盘的用户拍板/纠正/选择，不改已有内容；"
    "② progress：Current/Next 必须具体（写清楚做什么、做到哪一步），不写“继续推进”之类的废话；"
    "③ findings：只记对未来有用的内容（决策依据、踩坑教训、关键发现），不记流水账/过程记录；"
    "④ spec.md / plan.md 不碗——那是 brainstorming / writing-plans 的职责，checkpoint 不越权。"
)

RESUME_PROCEED_CONTRACT: str = (
    "【Resume 继续契约】"
    "验证结果非 BROKEN，可以进入工作状态。"
    "① 已充分了解上下文，不需要再问“你想做什么”或“我们继续哪里”；"
    "② 直接从 progress.md 的 Current/Next 接续，主动提出下一步行动；"
    "③ 若有 DRIFT 项，在 context.md 中更新对应路径/分支信息再继续。"
)

RESUME_BLOCKED_CONTRACT: str = (
    "【Resume 阻塞契约 — 严禁进入工作状态】"
    "存在 ❌ BROKEN 资源，环境验证未通过。"
    "① 只输出阻塞报告（哪些资源不可用、原因）；"
    "② 等待用户决策（修复环境 / 更新 context.md / 强制跳过）；"
    "③ 不得执行 progress.md 中 Current/Next 列出的任何任务；"
    "④ 不得假设问题已解决并继续推进。"
)

# ---------------------------------------------------------------------------
# 1. slugify — 话题名 → 文件夹名
# ---------------------------------------------------------------------------

# 允许保留的字符：CJK（一-鿿）、字母数字、连字符
_KEEP_RE = re.compile(r"[^\w一-鿿-]", re.UNICODE)
# 连续下划线/连字符 → 单连字符
_MULTI_DASH_RE = re.compile(r"[-_]{2,}")


def slugify(topic: str) -> str:
    """将话题名转换为文件夹名。

    规则：
    - 全小写（英文）
    - 空格/标点 → `-`
    - 保留 CJK 字符、字母数字、连字符
    - 折叠连续 `--`
    - 修剪首尾 `-`
    """
    # 先小写
    s = topic.strip().lower()
    # 把非保留字符替换为 `-`
    s = _KEEP_RE.sub("-", s)
    # 折叠连续连字符（含下划线被替换后的）
    s = _MULTI_DASH_RE.sub("-", s)
    # 去掉首尾连字符
    s = s.strip("-")
    return s


# ---------------------------------------------------------------------------
# 2. do_create — 创建 work-folder
# ---------------------------------------------------------------------------

def do_create(work_folder_root: str, topic: str, *, now_fn) -> dict:
    """创建 work-folder。

    Args:
        work_folder_root: work-folder 根目录（绝对路径）。
        topic:           工作主题，用于生成 slug 和初始 goal。
        now_fn:          返回 datetime-like 对象的函数（注入，便于测试）。

    Returns:
        - 新建：{"created": True, "path": abs, "seeded": [...], "drafting": SAVE_CONTRACT}
        - 已存在：{"created": False, "path": abs, "seeded": [], "note": "已存在"}
    """
    now = now_fn()
    date_str = now.strftime("%Y/%m/%d")
    slug = slugify(topic)
    folder = os.path.join(work_folder_root, date_str, slug)
    abs_path = str(Path(folder).resolve())

    if Path(folder).exists():
        return {
            "created": False,
            "path": abs_path,
            "seeded": [],
            "note": "已存在",
        }

    now_hm = now.strftime("%Y-%m-%d %H:%M")
    seeded = _art.ensure_folder(
        folder,
        goal=topic,
        status="brainstorming",
        phase="",
        now=now_hm,
    )

    return {
        "created": True,
        "path": abs_path,
        "seeded": seeded,
        "drafting": SAVE_CONTRACT,
    }


# ---------------------------------------------------------------------------
# 3. do_save — 保存 checkpoint
# ---------------------------------------------------------------------------

def _read_progress_fields(folder: str) -> tuple[str, str, str]:
    """从 progress.md 提取 goal / status / phase（best-effort，失败返回空串）。"""
    md = _art.read_artifact(folder, "progress.md") or ""

    def _extract(label: str) -> str:
        m = re.search(rf"\*\*{label}:\*\*\s*(.+)", md)
        return m.group(1).strip() if m else ""

    return _extract("Goal"), _extract("Status"), _extract("Phase")


def do_save(
    folder: str,
    *,
    now_fn,
    summary: str = "checkpoint",
    context_snapshot: str | None = None,
    resume_fields: dict | None = None,
    golden_order_additions: str | None = None,
    findings_addition: str | None = None,
) -> dict:
    """保存 checkpoint 到 work-folder。

    Args:
        folder:                 work-folder 绝对路径（必须存在）。
        now_fn:                 返回 datetime-like 的函数（注入）。
        summary:                changelog 摘要说明。
        context_snapshot:       若给定，覆盖写入 context.md。
        resume_fields:          传给 gen_resume_guide 的字段 dict；None 时从 progress.md 推导。
        golden_order_additions: 追加到 golden-order.md 的文字块。
        findings_addition:      追加到 findings.md 的文字块。

    Returns:
        {"saved": True, "folder": abs, "written": [files], "contract": SAVE_CONTRACT}

    Raises:
        FileNotFoundError: folder 不存在。
    """
    if not Path(folder).exists():
        raise FileNotFoundError(f"work-folder 不存在: {folder}")

    abs_path = str(Path(folder).resolve())
    now = now_fn()
    now_hm = now.strftime("%H:%M")
    written: list[str] = []

    # 幂等种子（补全缺失的 progress.md / context.md）
    _art.ensure_folder(folder, now=now.strftime("%Y-%m-%d %H:%M"))

    # context.md — 快照写入
    if context_snapshot is not None:
        _art.write_context_snapshot(folder, context_snapshot)
        written.append("context.md")

    # golden-order.md — 追加
    if golden_order_additions:
        existing = _art.read_artifact(folder, "golden-order.md") or ""
        updated = existing + golden_order_additions
        _art.write_artifact(folder, "golden-order.md", updated)
        written.append("golden-order.md")

    # findings.md — 追加
    if findings_addition:
        existing = _art.read_artifact(folder, "findings.md") or ""
        updated = existing + findings_addition
        _art.write_artifact(folder, "findings.md", updated)
        written.append("findings.md")

    # progress.md changelog 行
    _art.append_changelog(folder, time=now_hm, action="checkpoint", detail=summary)
    if "progress.md" not in written:
        written.append("progress.md")

    # CLAUDE.md / AGENTS.md — Resume Guide
    if resume_fields is not None:
        rf = resume_fields
    else:
        goal, status, phase = _read_progress_fields(folder)
        rf = {
            "goal": goal,
            "status": status,
            "phase": phase,
            "wf_abs": abs_path,
            "key_context": "",
            "now": now.strftime("%Y-%m-%d %H:%M"),
        }

    # 确保必填字段有默认值
    rf.setdefault("goal", "")
    rf.setdefault("status", "")
    rf.setdefault("phase", "")
    rf.setdefault("wf_abs", abs_path)
    rf.setdefault("key_context", "")
    rf.setdefault("now", now.strftime("%Y-%m-%d %H:%M"))

    guide_files = _art.gen_resume_guide(folder, **rf)
    written.extend(guide_files)

    return {
        "saved": True,
        "folder": abs_path,
        "written": written,
        "contract": SAVE_CONTRACT,
    }


# ---------------------------------------------------------------------------
# 4. do_resume — 恢复 work-folder（核心不变量：BROKEN → blocked=True）
# ---------------------------------------------------------------------------

def _now_hm(now) -> str:
    """从 datetime-like 对象提取 HH:MM 字符串。"""
    return now.strftime("%H:%M")


def do_resume(folder: str, *, now_fn, probe_fn=None) -> dict:
    """从 work-folder 恢复工作状态，执行环境验证。

    核心不变量（服务端强制）：
        overall == BROKEN  →  blocked=True，契约 = RESUME_BLOCKED_CONTRACT
        otherwise          →  blocked=False，契约 = RESUME_PROCEED_CONTRACT

    Args:
        folder:   work-folder 绝对路径。
        now_fn:   返回 datetime-like 的函数（注入）。
        probe_fn: 路径探针 `(path) -> dict`；默认 fs_git_probe。

    Returns:
        ok=True:
            {"ok": True, "folder": abs, "loaded": {...}, "verification": {...},
             "blocked": bool, "resume_report": str, "contract": str}
        ok=False（folder 不存在 / 缺关键文件）:
            {"ok": False, "error": str, "blocked": True}
    """
    if probe_fn is None:
        probe_fn = _ver.fs_git_probe

    # --- 前置验证 ---
    folder_path = Path(folder)
    if not folder_path.exists():
        return {
            "ok": False,
            "error": f"work-folder 不存在: {folder}",
            "blocked": True,
        }

    has_progress = (folder_path / "progress.md").exists()
    has_claude = (folder_path / "CLAUDE.md").exists()
    if not has_progress and not has_claude:
        return {
            "ok": False,
            "error": "work-folder 缺少 progress.md 和 CLAUDE.md，无法恢复",
            "blocked": True,
        }

    abs_path = str(folder_path.resolve())
    now = now_fn()
    now_hm_str = _now_hm(now)

    # --- 加载 artifacts ---
    loaded: dict[str, str | None] = {
        "claude":       _art.read_artifact(folder, "CLAUDE.md"),
        "progress":     _art.read_artifact(folder, "progress.md"),
        "context":      _art.read_artifact(folder, "context.md"),
        "findings":     _art.read_artifact(folder, "findings.md"),
        "golden_order": _art.read_artifact(folder, "golden-order.md"),
    }

    # --- 环境验证 ---
    context_md = loaded["context"] or ""
    resources = _ver.parse_context_paths(context_md)
    verdicts = _ver.verify_env(resources, probe_fn=probe_fn)
    overall = _ver.overall_level(verdicts)

    # 核心不变量
    blocked = (overall == _ver.BROKEN)

    # 统计各级别数量（用于 changelog detail）
    n_match = sum(1 for v in verdicts if v.level == _ver.MATCH)
    n_drift = sum(1 for v in verdicts if v.level == _ver.DRIFT)
    n_broken = sum(1 for v in verdicts if v.level == _ver.BROKEN)

    # --- Changelog 追加 ---
    _art.append_changelog(
        folder,
        time=now_hm_str,
        action="resume",
        detail=f"环境验证: {n_match}✅ {n_drift}⚠️ {n_broken}❌",
    )

    # --- 构建 resume_report ---
    level_icon = {"MATCH": "✅", "DRIFT": "⚠️", "BROKEN": "❌"}
    verdict_lines = "\n".join(
        f"  {level_icon.get(v.level, '?')} {v.name} ({v.path}) — {v.detail}"
        for v in verdicts
    ) or "  （无关键路径资源）"

    resume_report = (
        f"[Resume 报告]\n"
        f"Work folder: {abs_path}\n"
        f"环境验证总体: {overall}\n"
        f"\n"
        f"资源明细:\n"
        f"{verdict_lines}\n"
        f"\n"
        f"{'⚠️ 存在 BROKEN 资源，已阻塞，等待用户决策。' if blocked else '✅ 验证通过，可以继续工作。'}"
    )

    # --- 组装返回 ---
    return {
        "ok": True,
        "folder": abs_path,
        "loaded": loaded,
        "verification": {
            "overall": overall,
            "verdicts": [asdict(v) for v in verdicts],
        },
        "blocked": blocked,
        "resume_report": resume_report,
        "contract": RESUME_BLOCKED_CONTRACT if blocked else RESUME_PROCEED_CONTRACT,
    }


# ---------------------------------------------------------------------------
# 5. do_list — 列举 work-folder 候选
# ---------------------------------------------------------------------------

def do_list(work_folder_root: str, *, limit: int = 10) -> dict:
    """列举最近的 active work-folder。

    Args:
        work_folder_root: work-folder 根目录。
        limit:            返回上限（默认 10）。

    Returns:
        {"candidates": [{path, status, mtime}, ...]}
    """
    candidates = _art.list_work_folders(work_folder_root)[:limit]
    return {"candidates": candidates}
