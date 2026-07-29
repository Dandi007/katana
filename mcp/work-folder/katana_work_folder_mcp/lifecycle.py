"""Work Folder lifecycle 的纯读取与公共契约。

所有 mutation 都由 :mod:`store` 经 GovernedKernel 执行。此模块只保留：

- Save / Resume 面向 agent 的判断契约；
- 扁平 ``wf-ID/`` 根目录的只读 list；
- 公共 ``folder_id`` 校验。

旧的日期目录、slug 和直接文件系统 mutation 已删除，避免形成第二套事实源。
"""

from __future__ import annotations

import re
from pathlib import Path

from katana_work_folder_mcp import artifacts as _art
from katana_work_folder_mcp.brief import BRIEF_NAME, BriefError, parse_brief

FOLDER_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")

SAVE_CONTRACT: str = (
    "【Save 判断契约】"
    "① golden-order：宁多勿漏，只补本次 session 中尚未落盘的用户拍板/纠正/选择，不改已有内容；"
    "② progress：Current/Next 必须具体（写清楚做什么、做到哪一步），不写“继续推进”之类的废话；"
    "③ findings：只记对未来有用的内容（决策依据、踩坑教训、关键发现），不记流水账/过程记录；"
    "④ spec.md / plan.md 不归 checkpoint 管理。"
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


def require_folder_id(folder_id: str) -> str:
    """校验并返回 canonical opaque folder ID。"""
    if not isinstance(folder_id, str) or not FOLDER_ID_RE.fullmatch(folder_id):
        raise ValueError(f"invalid folder_id: {folder_id}")
    return folder_id


def do_list(repo_root: str, *, limit: int = 10) -> dict:
    """按 mtime 倒序列出 active work folder，不返回任何物理路径。"""
    candidates = _art.list_work_folders(repo_root)[: max(0, limit)]
    for candidate in candidates:
        _enrich_with_brief(candidate, repo_root)
    return {"candidates": candidates}


def _enrich_with_brief(candidate: dict, repo_root: str) -> None:
    """用 ``_brief.md`` 就地 enrich，但只暴露语义字段。"""
    folder_id = require_folder_id(candidate["folder_id"])
    brief = Path(repo_root) / folder_id / BRIEF_NAME
    if not brief.is_file():
        return
    try:
        parsed = parse_brief(brief.read_text(encoding="utf-8"))
    except (BriefError, OSError):
        return
    frontmatter = parsed["frontmatter"]
    if frontmatter.get("id") != folder_id:
        return
    candidate["title"] = frontmatter.get("title", "")
    candidate["goal"] = parsed["goal"]
    candidate["brief_status"] = frontmatter.get("status", "")
    updated = frontmatter.get("updated", "")
    candidate["updated"] = (
        updated.isoformat() if hasattr(updated, "isoformat") else str(updated)
    )
