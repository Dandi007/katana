"""Sweep 报告渲染（v2 三轴 CaseResult 格式）。"""
import datetime


def render_report(results, *, branch, sha, jobs, total_s,
                  overall_verdict: str = "") -> str:
    """渲染 sweep 报告为 Markdown。

    Args:
        results: list[CaseResult]（v2：含 axis_detail 三轴详情）
        branch: 分支名称
        sha: commit hash
        jobs: 并行 job 数
        total_s: 总耗时（秒）
        overall_verdict: 整体拍板意见（可选）

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
        # 兼容 v1 CaseResult（detail 字段）和 v2（axis_detail 字段）
        detail_str = _extract_detail(r)
        kept = getattr(r, "kept_dir", "") or ""
        if kept:
            detail_str += f" (kept: {kept})"
        detail_str = detail_str[:120].replace("|", "\\|")
        attribution = getattr(r, "attribution", "") or "—"
        lines.append(
            f"| {r.skill}#{r.case_id} | {r.status} | {attribution} "
            f"| {r.attempts} | {r.duration_s:.0f}s | {r.model or '—'} | {detail_str} |"
        )

    # Skipped 节
    lines += ["", "## Skipped", ""]
    skipped = by("SKIP")
    if skipped:
        lines += [f"- {r.skill}#{r.case_id}: {getattr(r, 'detail', '')}" for r in skipped]
    else:
        lines.append("- none")

    # NEEDS-REVIEW 节
    lines += ["", "## NEEDS-REVIEW", ""]
    needs_review = by("NEEDS-REVIEW")
    if needs_review:
        for r in needs_review:
            lines.append(f"### {r.skill}#{r.case_id}")
            # 兼容 v1 verdict_result 和 v2 axis_detail["semantic"]
            verdict = getattr(r, "verdict_result", None) or _get_semantic(r)
            for item in (verdict or {}).get("items", []):
                lines.append(
                    f"- [{item['answer']}] {item['q']} — {item.get('evidence', '')}"
                )
    else:
        lines.append("- none")

    # Overall Verdict 节
    lines += ["", "## Overall Verdict", "", overall_verdict or "_(not run)_", ""]

    return "\n".join(lines)


def _extract_detail(r) -> str:
    """从 CaseResult 提取 detail 字符串（兼容 v1/v2）。"""
    # v1 直接有 detail 字段
    direct = getattr(r, "detail", "") or ""
    if direct:
        return direct
    # v2：从 axis_detail 里拼失败项
    axis = getattr(r, "axis_detail", {}) or {}
    parts = []
    for axis_name in ("process", "filesystem"):
        for item in axis.get(axis_name, []):
            if not item.get("ok"):
                parts.append(f"{axis_name}/{item['type']}: {item.get('detail', '')}")
    return "; ".join(parts) if parts else ""


def _get_semantic(r) -> dict | None:
    """从 v2 axis_detail 取 semantic verdict dict。"""
    axis = getattr(r, "axis_detail", {}) or {}
    sem = axis.get("semantic")
    if isinstance(sem, dict):
        return sem
    return None
