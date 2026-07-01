"""reindex.py — 扫全库 _brief.md 生成 INDEX.md（Reindexer 节点，机械）。

回溯工具链的 ④ Reindexer：扫 root 下所有 work folder 的 `_brief.md`，
用 brief.py 的 parse_brief 解析，按 updated 倒序生成顶层 INDEX.md。

约束：纯 Python + pyyaml（复用 brief.py），无 fs 限制（与 workflow agent 不同）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from katana_work_folder_mcp.brief import BRIEF_NAME, BriefError, parse_brief

INDEX_NAME = "INDEX.md"


def collect_briefs(root: str, return_errors: bool = False):
    """递归扫 root 下所有 _brief.md，parse 返回 entries 列表。

    每条 entry: {"folder": str, "fm": dict, "goal": str}。
    解析失败的跳过（若 return_errors=True，额外返回 errors 列表）。
    """
    entries: list[dict] = []
    errors: list[str] = []
    root_p = Path(root)
    for brief in root_p.rglob(BRIEF_NAME):
        folder = str(brief.parent)
        try:
            r = parse_brief(brief.read_text(encoding="utf-8"))
        except BriefError as e:
            errors.append(f"{folder}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — 任何读取/解析异常都跳过，不中断全量
            errors.append(f"{folder}: {e}")
            continue
        entries.append({"folder": folder, "fm": r["frontmatter"], "goal": r["goal"]})
    if return_errors:
        return entries, errors
    return entries


def render_index(entries: list[dict]) -> str:
    """按 updated 倒序生成 INDEX.md 内容（markdown 表格）。

    每行：updated · status · id · title · goal · folder
    """
    sorted_entries = sorted(
        entries,
        key=lambda e: e["fm"].get("updated", ""),
        reverse=True,
    )
    lines = [
        "# Work Folder INDEX",
        "",
        f"> 共 {len(sorted_entries)} 个 work folder，按 updated 倒序。由 wf-reindex 自动生成，勿手改。",
        "",
        "| updated | status | id | title | goal | folder |",
        "|---|---|---|---|---|---|",
    ]
    for e in sorted_entries:
        fm = e["fm"]
        updated = fm.get("updated", "")
        status = fm.get("status", "")
        id_ = fm.get("id", "")
        title = fm.get("title", "")
        goal = (e["goal"] or "").replace("|", "\\|")
        # folder 用相对根的短路径更可读，但这里无根信息，留绝对路径末两段
        folder = e["folder"]
        lines.append(f"| {updated} | {status} | {id_} | {title} | {goal} | `{folder}` |")
    lines.append("")
    return "\n".join(lines)


def reindex(root: str, dry_run: bool = False) -> dict:
    """扫 root 生成 INDEX.md。返回 {indexed, skipped, errors, index_path, preview?}。

    - dry_run=True：不写文件，preview 字段含将生成的 INDEX 内容。
    - skipped：root 下无 _brief.md 的 folder 数（统计用，需扫目录）。
    """
    root_p = Path(root)
    entries, errors = collect_briefs(str(root_p), return_errors=True)

    # 统计无 brief 的 folder 数（work folder = YYYY/MM/DD/<slug> 四级嵌套叶子）。
    # 只把"含 progress.md 但缺 _brief.md"的目录算作 skipped（normalizer 该补 brief 的目标），
    # 避免把中间目录（YYYY/、MM/、DD/）和 artifacts/ 等误算。
    brief_folders = {Path(e["folder"]) for e in entries}
    skipped = 0
    for d in root_p.rglob("progress.md"):
        folder = d.parent
        if folder in brief_folders:
            continue
        skipped += 1

    md = render_index(entries)
    index_path = root_p / INDEX_NAME

    result = {
        "indexed": len(entries),
        "skipped": skipped,
        "errors": errors,
        "index_path": str(index_path),
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
        print(f"[reindex] wrote {r['index_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
