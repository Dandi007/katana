"""brief_ops.py — _brief.md 的 folder 级维护操作（seed / touch），MCP 与外部 CLI 共用。

`brief.py` 是纯 schema（parse/render/validate）；本模块加 folder IO 层：
  - ``seed_brief``  : folder 缺 `_brief.md` 时按种子字段生成（幂等，已存在则 no-op）
  - ``touch_brief`` : 刷新 `updated`（+可选 status 拉回 active），保留既有 frontmatter/goal/summary

设计约束（与 brief.py 一致）：可用 pyyaml（已是包依赖）；不引入 server/config/LLM 依赖，
便于 MCP、`wf-touch` CLI、以及 session-harvest 等外部消费者复用同一份 SSoT。

`updated` 一律写成带引号的 ISO 字符串（str），避免 YAML 把裸日期解析成 ``datetime.date``、
后续 reindex 排序时混类型抛 TypeError（见 reindex.py 的 _updated_key 与 F5）。
"""
from __future__ import annotations

import re
from pathlib import Path

from katana_work_folder_mcp.brief import (
    BRIEF_NAME,
    BriefError,
    parse_brief,
    render_brief,
)

_FOLDER_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")


def _require_folder_id(folder_id: str) -> str:
    if not _FOLDER_ID_RE.fullmatch(folder_id):
        raise ValueError(f"invalid folder_id: {folder_id}")
    return folder_id


def seed_brief(
    folder: str,
    *,
    folder_id: str,
    title: str,
    goal: str,
    status: str = "active",
    now: str,
    summary: str = "",
    tags=(),
    kind: str = "",
    links=(),
) -> bool:
    """folder 缺 `_brief.md` 时生成之；已存在则不覆盖。

    Returns:
        True  — 本次新建了 `_brief.md`
        False — 已存在（no-op）
    """
    brief = Path(folder) / BRIEF_NAME
    if brief.exists():
        return False
    folder_id = _require_folder_id(folder_id)
    text = render_brief(
        id=folder_id,
        title=title,
        status=status,
        created=now,
        updated=now,
        goal=goal,
        summary=summary,
        tags=tags,
        kind=kind,
        links=links,
    )
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text(text, encoding="utf-8")
    return True


def touch_brief(
    folder: str,
    *,
    folder_id: str,
    now: str,
    reactivate: bool = True,
    seed_title: str | None = None,
    seed_goal: str | None = None,
) -> bool:
    """刷新 folder 的 `_brief.md`：把 ``updated`` 拉到 now，可选把 status 拉回 active。

    - 保留既有 frontmatter 其余字段 + goal + summary（round-trip parse→render）。
    - `reactivate=True` 且当前 status ∈ {paused, archived} → 拉回 active；
      completed 保持不动（已完成的工作不因一次写入被复活）。
    - brief 缺失：若给了 ``seed_title``/``seed_goal`` 则 best-effort seed，否则 no-op。
    - brief 损坏（无 frontmatter / YAML 非法）：不吞不改，返回 False。

    Returns:
        True  — brief 被写入（touch 或 seed）
        False — 未改动（缺失且无种子 / 损坏）
    """
    brief = Path(folder) / BRIEF_NAME
    if not brief.exists():
        if seed_title is not None and seed_goal is not None:
            return seed_brief(
                folder,
                folder_id=folder_id,
                title=seed_title,
                goal=seed_goal,
                status="active",
                now=now,
            )
        return False

    try:
        r = parse_brief(brief.read_text(encoding="utf-8"))
    except BriefError:
        return False

    fm = r["frontmatter"]
    folder_id = _require_folder_id(folder_id)
    if fm.get("id") != folder_id:
        raise ValueError(
            f"brief id does not match folder_id: {fm.get('id')} != {folder_id}"
        )
    status = str(fm.get("status") or "active")
    if reactivate and status in ("paused", "archived"):
        status = "active"

    text = render_brief(
        id=folder_id,
        title=fm.get("title", ""),
        status=status,
        created=_as_iso(fm.get("created")) or now,
        updated=now,
        goal=r["goal"],
        summary=r["summary"],
        tags=fm.get("tags") or (),
        kind=fm.get("kind") or "",
        links=fm.get("links") or (),
    )
    brief.write_text(text, encoding="utf-8")
    return True


def _as_iso(v) -> str:
    """把 frontmatter 里的日期值统一成 ISO 字符串（datetime.date → isoformat）。"""
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def main(argv=None) -> int:
    """CLI: ``wf-touch <folder> [<folder>...] [--date YYYY-MM-DD] [--no-reactivate]``。

    刷新每个 folder 的 `_brief.md`（updated 拉到 --date、可选复活 status）。
    供 session-harvest 等外部消费者在追加 progress 后同步 brief 层用。
    缺 brief 且未给种子 → 跳过（best-effort，不新建）。
    """
    import datetime
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("usage: wf-touch <folder> [<folder>...] [--date YYYY-MM-DD] [--no-reactivate]")
        return 2

    reactivate = "--no-reactivate" not in args
    args = [a for a in args if a != "--no-reactivate"]

    date = None
    if "--date" in args:
        i = args.index("--date")
        try:
            date = args[i + 1]
            del args[i:i + 2]
        except IndexError:
            print("--date 需要一个 YYYY-MM-DD 参数")
            return 2
    if date is None:
        date = datetime.date.today().isoformat()

    folders = [a for a in args if not a.startswith("--")]
    if not folders:
        print("usage: wf-touch <folder> [<folder>...] [--date YYYY-MM-DD] [--no-reactivate]")
        return 2

    touched = 0
    for f in folders:
        folder_id = Path(f).name
        if touch_brief(
            f,
            folder_id=folder_id,
            now=date,
            reactivate=reactivate,
        ):
            touched += 1
            print(f"[touched] {f}")
        else:
            print(f"[skip]    {f}（缺 _brief.md 或损坏）")
    print(f"[wf-touch] touched={touched}/{len(folders)} date={date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
