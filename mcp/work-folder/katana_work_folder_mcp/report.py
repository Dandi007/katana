"""report.py — wf-report 汇总 workflow JSON + git status 生成 review 报告（Reporter 节点）。

回溯工具链的 ⑤ Reporter：读 backfill.workflow.js 返回的 JSON report（wrapper 落盘到
/tmp/wf-backfill-report.json）+ vault 的 git status，生成事后 review 报告。

约束：纯 Python；可调 git（subprocess）获取 status，或接受外部传入的 status 行。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def summarize_report(report: dict) -> dict:
    """从 workflow JSON 提取统计 + 异常清单。

    输入 report 形如 {classified, normalized, explored, failed, reports:[{folder,created,moved,skipped,note}]}。
    """
    reps = report.get("reports", []) or []
    created_total = sum(len(r.get("created", [])) for r in reps)
    moved_total = sum(len(r.get("moved", [])) for r in reps)
    skipped = sum(1 for r in reps if r.get("skipped"))
    # failed：note 里含 ERROR，或 workflow 顶层 failed 字段
    errors = [f"{r.get('folder', '?')}: {r.get('note', '')}"
              for r in reps if "ERROR" in str(r.get("note", "")).upper()]
    failed = report.get("failed", len(errors))
    return {
        "folders": len(reps),
        "classified": report.get("classified", len(reps)),
        "normalized": report.get("normalized", len(reps) - skipped - len(errors)),
        "explored": report.get("explored", 0),
        "created_total": created_total,
        "moved_total": moved_total,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


def render_report(summary: dict, git_status: list[str]) -> str:
    """渲染 markdown review 报告。"""
    lines = ["# Work Folder 回溯 Review 报告", ""]
    lines.append("## 统计")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for k in ("folders", "classified", "normalized", "explored",
              "created_total", "moved_total", "skipped", "failed"):
        lines.append(f"| {k} | {summary.get(k, 0)} |")
    lines.append("")

    if summary.get("errors"):
        lines.append("## 异常清单")
        lines.append("")
        for e in summary["errors"][:50]:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("## git 改动")
    lines.append("")
    if git_status:
        # 按改动类型分组计数
        added = [g for g in git_status if g.startswith("??")]
        modified = [g for g in git_status if g.startswith(" M") or g.startswith("M ")]
        renamed = [g for g in git_status if g.startswith("R ")]
        lines.append(f"- 新增(untracked): {len(added)}")
        lines.append(f"- 修改(modified): {len(modified)}")
        lines.append(f"- 重命名(git mv → artifacts): {len(renamed)}")
        lines.append("")
        lines.append("<details><summary>明细</summary>")
        lines.append("")
        for g in git_status[:200]:
            lines.append(f"```\n{g}\n```")
        lines.append("")
        lines.append("</details>")
    else:
        lines.append("无 git 改动（dry-run 或无落盘）。")
    lines.append("")
    return "\n".join(lines)


def _git_status(root: str) -> list[str]:
    """跑 git status --short 取改动行。失败返回空列表。"""
    try:
        out = subprocess.run(
            ["git", "-C", root, "status", "--short", "--"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return [ln for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return []


def report(report_path: str, root: str, git_status: list[str] | None = None) -> str:
    """读 report JSON + git status → 返回 review 报告文本。"""
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))
    summary = summarize_report(data)
    if git_status is None:
        git_status = _git_status(root)
    return render_report(summary, git_status)


def main(argv=None) -> int:
    """CLI: wf-report <report.json> --root <vault-root> [--out <path>]"""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: wf-report <report.json> --root <vault-root> [--out <path>]")
        return 2
    report_path = args[0]
    root = "."
    out = None
    i = 1
    while i < len(args):
        if args[i] == "--root":
            root = args[i + 1]; i += 2
        elif args[i] == "--out":
            out = args[i + 1]; i += 2
        else:
            i += 1
    md = report(report_path, root)
    if out:
        Path(out).write_text(md, encoding="utf-8")
        print(f"[report] wrote {out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
