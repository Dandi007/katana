"""Sweep 报告渲染（spec §9 格式）。"""
import datetime


def render_report(results, *, branch, sha, jobs, total_s,
                  overall_verdict: str = "") -> str:
    """渲染 sweep 报告为 Markdown。

    Args:
        results: list[CaseResult] 测试结果列表
        branch: 分支名称
        sha: commit hash
        jobs: 并行 job 数
        total_s: 总耗时（秒）
        overall_verdict: 整体拍板意见

    Returns:
        Markdown 格式的报告字符串
    """
    by = lambda s: [r for r in results if r.status == s]

    lines = [
        "# Contract Sweep Report", "",
        f"- branch: `{branch}` @ `{sha}`",
        f"- date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- jobs: {jobs} / total: {total_s:.0f}s",
        f"- **PASS {len(by('PASS'))} / FAIL {len(by('FAIL'))} / "
        f"SKIP {len(by('SKIP'))} / NEEDS-REVIEW {len(by('NEEDS-REVIEW'))}**", "",
        "| case | result | 归因 | attempts | 耗时 | model | detail |",
        "|---|---|---|---|---|---|---|",
    ]

    # 汇总表
    for r in results:
        detail = (r.detail or "")[:120].replace("|", "\\|")
        if r.kept_dir:
            detail += f" (kept: {r.kept_dir})"
        lines.append(f"| {r.skill}#{r.case_id} | {r.status} | {r.attribution or '—'} "
                     f"| {r.attempts} | {r.duration_s:.0f}s | {r.model or '—'} | {detail} |")

    # Skipped 节
    lines += ["", "## Skipped", ""]
    skipped = by("SKIP")
    if skipped:
        lines += [f"- {r.skill}#{r.case_id}: {r.detail}" for r in skipped]
    else:
        lines.append("- none")

    # NEEDS-REVIEW 节
    lines += ["", "## NEEDS-REVIEW", ""]
    needs_review = by("NEEDS-REVIEW")
    if needs_review:
        for r in needs_review:
            lines.append(f"### {r.skill}#{r.case_id}")
            for item in (r.verdict_result or {}).get("items", []):
                lines.append(f"- [{item['answer']}] {item['q']} — {item.get('evidence', '')}")
    else:
        lines.append("- none")

    # Overall Verdict 节
    lines += ["", "## Overall Verdict", "", overall_verdict or "_(not run)_", ""]

    return "\n".join(lines)
