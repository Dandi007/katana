"""brief.py — _brief.md schema 的 parse/render/validate/lint（MCP 与回溯工具共用）。

work folder 的"身份证"文件 `_brief.md`：
  - YAML frontmatter（id/title/status/created/updated + 可选 tags/kind/links）
  - 正文：一行 **Goal:** + 摘要

约束：可用 pyyaml（已是包依赖）；不引入 server/config/LLM 依赖，便于 CLI 与回溯工具复用。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

BRIEF_NAME = "_brief.md"
VALID_STATUS = {"active", "paused", "archived", "completed"}
REQUIRED_FIELDS = ("id", "title", "status", "created", "updated")

# frontmatter 块：开头 ---\n ... \n---，其后为正文
_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
# 只取 **Goal:** 同一行内容（不跨行），空 goal 时捕获空串而非误吞下一行摘要
_GOAL_RE = re.compile(r"\*\*Goal:\*\*[ \t]*([^\n]*)")


class BriefError(Exception):
    """brief 解析失败（缺 frontmatter / YAML 非法）。"""


def parse_brief(text: str) -> dict:
    """解析 _brief.md 文本为 {frontmatter: dict, goal: str, summary: str}。

    frontmatter 缺失或 YAML 非法时 raise BriefError。
    """
    m = _FM_RE.match(text.lstrip("﻿"))
    if not m:
        raise BriefError("缺少 YAML frontmatter")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise BriefError(f"frontmatter YAML 非法: {e}") from e
    if not isinstance(fm, dict):
        raise BriefError("frontmatter 不是映射")
    body = m.group(2)
    gm = _GOAL_RE.search(body)
    goal = gm.group(1).strip() if gm else ""
    summary = _GOAL_RE.sub("", body).strip()
    return {"frontmatter": fm, "goal": goal, "summary": summary}


def render_brief(
    *,
    id,
    title,
    status,
    created,
    updated,
    goal,
    summary,
    tags=(),
    kind="",
    links=(),
) -> str:
    """生成 _brief.md 文本；与 parse_brief round-trip。"""
    fm = {
        "id": id,
        "title": title,
        "status": status,
        "created": created,
        "updated": updated,
        "tags": list(tags),
        "kind": kind,
        "links": list(links),
    }
    y = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{y}\n---\n\n**Goal:** {goal}\n\n{summary}\n"


def validate_brief(text: str) -> list[str]:
    """校验 brief 文本，返回问题清单（空 = 合规）。"""
    problems: list[str] = []
    try:
        r = parse_brief(text)
    except BriefError as e:
        return [str(e)]
    fm = r["frontmatter"]
    for f in REQUIRED_FIELDS:
        if not fm.get(f):
            problems.append(f"缺少 frontmatter 字段: {f}")
    st = fm.get("status")
    if st and st not in VALID_STATUS:
        problems.append(f"status 非法: {st}（应为 {sorted(VALID_STATUS)}）")
    if not r["goal"]:
        problems.append("缺少 **Goal:** 行")
    return problems


def lint_folder(folder: str) -> dict:
    """lint 一个 work folder：core = _brief.md（存在且合规）+ progress.md（存在）。

    返回 {"folder", "ok": bool, "problems": [...]}。
    """
    p = Path(folder)
    problems: list[str] = []
    brief = p / BRIEF_NAME
    if not brief.exists():
        problems.append(f"缺少 {BRIEF_NAME}")
    else:
        problems += validate_brief(brief.read_text(encoding="utf-8"))
    if not (p / "progress.md").exists():
        problems.append("缺少 progress.md")
    return {"folder": str(p.resolve()), "ok": not problems, "problems": problems}


def main(argv=None) -> int:
    """CLI: wf-lint <folder> [<folder>...]；有问题返回 1。"""
    import sys

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: wf-lint <folder> [<folder>...]")
        return 2
    rc = 0
    for f in args:
        r = lint_folder(f)
        status = "OK" if r["ok"] else "FAIL"
        print(f"[{status}] {r['folder']}")
        for pb in r["problems"]:
            print(f"    - {pb}")
        if not r["ok"]:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
