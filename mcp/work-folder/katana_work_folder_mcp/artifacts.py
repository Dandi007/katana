"""artifacts.py — work-folder artifact I/O layer (L0).

模块职责：
  - 模板渲染（progress.md / context.md / CLAUDE.md / AGENTS.md）
  - artifact 读写（read_artifact / write_artifact）
  - 幂等 changelog 追加（insert_changelog_row / append_changelog）
  - work-folder 初始化（ensure_folder）
  - Resume Guide 生成（gen_resume_guide）
  - work-folder 列举（list_work_folders）

约束：仅用标准库 + re。不引入 server/config/LLM 依赖。
"""
from __future__ import annotations

import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# 纯函数 — 无 IO，可直接单元测试
# ---------------------------------------------------------------------------

def render_progress_skeleton(
    *,
    goal: str,
    status: str,
    phase: str,
    now: str,
) -> str:
    """生成 progress.md 初始骨架（符合 artifact-formats.md 规范）。

    包含：
      - 标题 # Progress
      - 元信息字段（Goal / Status / Phase / Updated）
      - ## Completed / ## Current / ## Blocked（含 - None） / ## Next
      - ## Changelog 表格（表头 + 分隔符，无数据行）
    """
    return (
        "# Progress\n"
        "\n"
        f"**Goal:** {goal}\n"
        f"**Status:** {status}\n"
        f"**Phase:** {phase}\n"
        f"**Updated:** {now}\n"
        "\n"
        "## Completed\n"
        "- \n"
        "\n"
        "## Current\n"
        "- \n"
        "\n"
        "## Blocked\n"
        "- None\n"
        "\n"
        "## Next\n"
        "- \n"
        "\n"
        "## Changelog\n"
        "| Time | Action | Detail |\n"
        "|------|--------|--------|\n"
    )


def render_context_skeleton(*, now: str) -> str:
    """生成 context.md 初始骨架（符合 artifact-formats.md 规范）。

    包含：
      - 标题 # Context
      - **Updated:** 字段
      - ## 工作上下文
      - ## 关键路径 表格（含表头 + 分隔符）
      - ## 环境信息
    """
    return (
        "# Context\n"
        "\n"
        f"**Updated:** {now}\n"
        "\n"
        "## 工作上下文\n"
        "- \n"
        "\n"
        "## 关键路径\n"
        "| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |\n"
        "|------|------------|------------|------|\n"
        "\n"
        "## 环境信息\n"
        "- \n"
    )


def render_resume_guide(
    *,
    goal: str,
    phase: str,
    status: str,
    wf_abs: str,
    key_context: str,
    decisions: str = "",
    issues: str = "",
    lessons: str = "",
    now: str,
) -> str:
    """生成 Resume Guide 内容（写入 CLAUDE.md / AGENTS.md）。

    空字段（decisions / issues / lessons）默认渲染为"暂无"。
    """
    def _or_zanwu(s: str) -> str:
        return s.strip() if s.strip() else "暂无"

    return (
        "# Resume Guide\n"
        "\n"
        f"> 由 checkpoint 自动生成。上次更新：{now}\n"
        "\n"
        "## Goal\n"
        f"{goal}\n"
        "\n"
        "## Status\n"
        f"- **Phase:** {phase}\n"
        f"- **Status:** {status}\n"
        f"- **Work folder:** {wf_abs}\n"
        "\n"
        "## Key Context\n"
        f"{key_context}\n"
        "\n"
        "## Key Decisions\n"
        f"{_or_zanwu(decisions)}\n"
        "\n"
        "## Known Issues\n"
        f"{_or_zanwu(issues)}\n"
        "\n"
        "## Lessons\n"
        f"{_or_zanwu(lessons)}\n"
        "\n"
        "## Resume Steps\n"
        "1. 阅读 progress.md 了解当前进度\n"
        "2. 阅读 context.md 了解环境状态\n"
        "3. 如有 spec.md / plan.md，阅读了解设计与计划\n"
        "4. 继续 progress.md 中 Current/Next 列出的任务\n"
    )


def changelog_row(time: str, action: str, detail: str) -> str:
    """生成单行 Changelog 表格数据行。"""
    return f"| {time} | {action} | {detail} |"


def insert_changelog_row(progress_md: str, row: str) -> str:
    """在 progress.md 的 Changelog 表格末尾插入一行（幂等）。

    - 若 row 已存在（逐字匹配）→ 原文不变
    - 若不存在 Changelog 表格 → 在文末追加完整 section
    - 表格已存在 → 在最后一个数据行/分隔符之后追加
    """
    # 幂等检查：已存在则直接返回
    if row in progress_md:
        return progress_md

    # 定位 ## Changelog section
    changelog_pattern = re.compile(r"(## Changelog\n.*?(?:\|[^\n]*\n)*)", re.DOTALL)
    match = changelog_pattern.search(progress_md)

    if match is None:
        # 没有 Changelog section → 追加完整 section
        fresh_section = (
            "\n## Changelog\n"
            "| Time | Action | Detail |\n"
            "|------|--------|--------|\n"
            f"{row}\n"
        )
        return progress_md.rstrip("\n") + "\n" + fresh_section
    else:
        # 在 section 末尾追加行
        end_pos = match.end()
        return progress_md[:end_pos] + row + "\n" + progress_md[end_pos:]


def parse_status(progress_md: str) -> str:
    """提取 progress.md 中 **Status:** 的值；不存在时返回空字符串。"""
    m = re.search(r"\*\*Status:\*\*\s*(.+)", progress_md)
    if m is None:
        return ""
    return m.group(1).strip()


# ---------------------------------------------------------------------------
# IO 函数
# ---------------------------------------------------------------------------

def read_artifact(folder: str, name: str) -> str | None:
    """读取 <folder>/<name> 内容；文件不存在时返回 None。"""
    p = Path(folder) / name
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def write_artifact(folder: str, name: str, content: str) -> None:
    """写入 <folder>/<name>（自动创建父目录）。"""
    p = Path(folder) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def ensure_folder(
    folder: str,
    *,
    goal: str = "",
    status: str = "brainstorming",
    phase: str = "",
    now: str = "",
) -> list[str]:
    """初始化 work-folder：创建目录，仅在缺失时 seed progress.md 和 context.md。

    返回实际创建的文件 basename 列表（如 ["progress.md", "context.md"]）。
    若两者均已存在返回 []。不触碰 goal/spec/plan/golden-order/findings。
    """
    Path(folder).mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    if read_artifact(folder, "progress.md") is None:
        content = render_progress_skeleton(goal=goal, status=status, phase=phase, now=now)
        write_artifact(folder, "progress.md", content)
        created.append("progress.md")

    if read_artifact(folder, "context.md") is None:
        content = render_context_skeleton(now=now)
        write_artifact(folder, "context.md", content)
        created.append("context.md")

    return created


def append_changelog(
    folder: str,
    *,
    time: str,
    action: str,
    detail: str,
) -> bool:
    """在 progress.md 的 Changelog 表格末尾追加一行（幂等）。

    - progress.md 不存在时先调用 ensure_folder 初始化
    - 返回 True 表示成功追加，False 表示该行已存在（幂等命中）
    """
    if read_artifact(folder, "progress.md") is None:
        ensure_folder(folder)

    current = read_artifact(folder, "progress.md") or ""
    row = changelog_row(time, action, detail)
    updated = insert_changelog_row(current, row)
    added = updated != current
    write_artifact(folder, "progress.md", updated)
    return added


def write_context_snapshot(folder: str, content: str) -> None:
    """覆盖 context.md（快照写入，非追加）。"""
    write_artifact(folder, "context.md", content)


def gen_resume_guide(
    folder: str,
    *,
    goal: str,
    phase: str,
    status: str,
    wf_abs: str,
    key_context: str = "",
    decisions: str = "",
    issues: str = "",
    lessons: str = "",
    now: str,
) -> list[str]:
    """生成 Resume Guide 并同时写入 CLAUDE.md 和 AGENTS.md（内容相同）。

    返回 ["CLAUDE.md", "AGENTS.md"]。
    """
    content = render_resume_guide(
        goal=goal,
        phase=phase,
        status=status,
        wf_abs=wf_abs,
        key_context=key_context,
        decisions=decisions,
        issues=issues,
        lessons=lessons,
        now=now,
    )
    write_artifact(folder, "CLAUDE.md", content)
    write_artifact(folder, "AGENTS.md", content)
    return ["CLAUDE.md", "AGENTS.md"]


def list_work_folders(root: str) -> list[dict]:
    """列举 root 下所有 work-folder（含 progress.md 或 CLAUDE.md 的目录）。

    过滤：排除 status == "completed" 的目录。
    排序：按 progress.md（或 CLAUDE.md）的 mtime 降序。
    返回：[{"path": <abs>, "status": <str>, "mtime": <float>}]
    """
    results: list[dict] = []
    root_path = Path(root)

    if not root_path.is_dir():
        return results

    # 递归遍历：真实 work folder 嵌在 YYYY/MM/DD/<topic>/ 多层下，非 root 直接子目录。
    # 命中含 progress.md/CLAUDE.md 的目录即视为 work-folder 叶子，不再下钻（work folder 不嵌套）；
    # 跳过隐藏目录（.git/.superpowers 等）。
    for dirpath, dirnames, _filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        entry = Path(dirpath)
        progress_file = entry / "progress.md"
        claude_file = entry / "CLAUDE.md"

        has_progress = progress_file.exists()
        has_claude = claude_file.exists()

        if not has_progress and not has_claude:
            continue

        # 命中 work-folder 叶子：剪枝，不再下钻其子目录
        dirnames[:] = []

        if has_progress:
            md = progress_file.read_text(encoding="utf-8")
            mtime = progress_file.stat().st_mtime
        else:
            md = ""
            mtime = claude_file.stat().st_mtime

        status = parse_status(md)

        if status == "completed":
            continue

        results.append({
            "path": str(entry.resolve()),
            "status": status,
            "mtime": mtime,
        })

    results.sort(key=lambda r: r["mtime"], reverse=True)
    return results
