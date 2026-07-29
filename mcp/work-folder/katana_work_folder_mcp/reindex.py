"""reindex.py — 扫全库 _brief.md 生成 INDEX.md（Reindexer 节点，机械）。

回溯工具链的 ④ Reindexer：扫 root 下所有 work folder 的 `_brief.md`，
用 brief.py 的 parse_brief 解析，按 updated 倒序生成顶层 INDEX.md。

约束：纯 Python + pyyaml（复用 brief.py），无 fs 限制（与 workflow agent 不同）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from katana_work_folder_mcp.brief import BRIEF_NAME, BriefError, parse_brief

INDEX_NAME = "INDEX.md"
_FOLDER_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")


def collect_briefs(root: str, return_errors: bool = False):
    """扫描 root 的扁平 ``wf-ID/`` 目录，parse 返回 entries 列表。

    每条 entry: {"folder_id": str, "fm": dict, "goal": str}。
    解析失败的跳过（若 return_errors=True，额外返回 errors 列表）。
    """
    entries: list[dict] = []
    errors: list[str] = []
    root_p = Path(root)
    if not root_p.is_dir():
        return (entries, errors) if return_errors else entries
    for folder in root_p.iterdir():
        if not folder.is_dir() or not _FOLDER_ID_RE.fullmatch(folder.name):
            continue
        brief = folder / BRIEF_NAME
        if not brief.is_file():
            continue
        try:
            r = parse_brief(brief.read_text(encoding="utf-8"))
        except BriefError as e:
            errors.append(f"{folder.name}/{BRIEF_NAME}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — 任何读取/解析异常都跳过，不中断全量
            errors.append(f"{folder.name}/{BRIEF_NAME}: {e}")
            continue
        brief_id = r["frontmatter"].get("id")
        if brief_id != folder.name:
            errors.append(
                f"{folder.name}/{BRIEF_NAME}: id mismatch ({brief_id} != {folder.name})"
            )
            continue
        entries.append({
            "folder_id": folder.name,
            "fm": r["frontmatter"],
            "goal": r["goal"],
        })
    if return_errors:
        return entries, errors
    return entries


def render_index(entries: list[dict]) -> str:
    """按 updated 倒序生成 INDEX.md 内容（markdown 表格）。

    每行：updated · status · id · title · goal
    """

    def _updated_key(e):
        """统一 updated 为 ISO 字符串排序。

        YAML 解析时，不带引号的 ``2026-07-01`` 会被解析成 ``datetime.date``，
        带引号的 ``"2026-07-01"`` 则是 str。混在一起 sorted() 会抛 TypeError。
        统一转成 ``YYYY-MM-DD`` 字符串再排，空值降到最后。
        """
        v = e["fm"].get("updated", "")
        if hasattr(v, "isoformat"):  # datetime.date / datetime.datetime
            return v.isoformat()
        return str(v) if v else ""

    sorted_entries = sorted(
        entries,
        key=_updated_key,
        reverse=True,
    )
    lines = [
        "# Work Folder INDEX",
        "",
        f"> 共 {len(sorted_entries)} 个 work folder，按 updated 倒序。由 wf-reindex 自动生成，勿手改。",
        "",
        "| updated | status | id | title | goal |",
        "|---|---|---|---|---|",
    ]
    for e in sorted_entries:
        fm = e["fm"]
        updated = _updated_key(e)  # 统一转 ISO 字符串（datetime.date → str）
        status = fm.get("status", "")
        id_ = fm.get("id", "")
        title = fm.get("title", "")
        goal = (e["goal"] or "").replace("|", "\\|")
        lines.append(f"| {updated} | {status} | {id_} | {title} | {goal} |")
    lines.append("")
    return "\n".join(lines)


def reindex(root: str, dry_run: bool = False) -> dict:
    """扫 root 生成 INDEX.md。返回 {indexed, skipped, errors, preview?}。

    - dry_run=True：不写文件，preview 字段含将生成的 INDEX 内容。
    - skipped：root 下无 _brief.md 的 folder 数（统计用，需扫目录）。
    """
    root_p = Path(root)
    entries, errors = collect_briefs(str(root_p), return_errors=True)

    brief_folders = {e["folder_id"] for e in entries}
    skipped = 0
    if root_p.is_dir():
        folders = [
            d for d in root_p.iterdir()
            if d.is_dir() and _FOLDER_ID_RE.fullmatch(d.name)
        ]
    else:
        folders = []
    for folder in folders:
        if folder.name in brief_folders or not (folder / "progress.md").is_file():
            continue
        skipped += 1

    md = render_index(entries)
    index_path = root_p / INDEX_NAME

    result = {
        "indexed": len(entries),
        "skipped": skipped,
        "errors": errors,
    }
    if dry_run:
        result["preview"] = md
    else:
        index_path.write_text(md, encoding="utf-8")
    return result


def main(argv=None) -> int:
    """CLI: wf-reindex <root> [--dry-run]"""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: wf-reindex <root> [--dry-run]")
        return 2
    dry = "--dry-run" in args
    root = next((a for a in args if not a.startswith("--")), None)
    if not root:
        print("usage: wf-reindex <root> [--dry-run]")
        return 2
    r = reindex(root, dry_run=dry)
    print(f"[reindex] indexed={r['indexed']} skipped={r['skipped']} errors={len(r['errors'])}")
    if r["errors"]:
        for e in r["errors"][:20]:
            print(f"  ! {e}")
    if dry:
        print("--- preview ---")
        print(r["preview"])
    else:
        print(f"[reindex] wrote {INDEX_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
