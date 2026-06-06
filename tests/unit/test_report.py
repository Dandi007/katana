"""Test sweep report rendering."""
from harness.case import CaseResult
from harness.report import render_report


def test_render_contains_all_sections():
    results = [
        CaseResult("query-hot", "wiki:query", "PASS", attempts=1,
                   duration_s=94.2, model="lingzhi/claude-opus-4-8"),
        CaseResult("xhs", "retrieval:xiaohongshu", "SKIP", detail="dir missing: profile"),
        CaseResult("ingest", "wiki:ingest", "FAIL", attempts=2, attribution="prompt",
                   detail="file_absent: unexpected", kept_dir="/tmp/x", duration_s=120,
                   model="lingzhi/claude-opus-4-8"),
        CaseResult("dr", "deep-research:deep-research", "NEEDS-REVIEW",
                   verdict_result={"items": [{"q": "报告每条 claim 有引用?", "answer": "no",
                                              "evidence": "第三节两条裸断言"}]}),
    ]
    md = render_report(results, branch="feat/x", sha="abc1234",
                       jobs=4, total_s=300.5, overall_verdict="整体可，注意 dr。")
    assert "PASS 1 / FAIL 1 / SKIP 1 / NEEDS-REVIEW 1" in md
    assert "| wiki:query#query-hot | PASS |" in md
    assert "prompt" in md and "/tmp/x" in md
    assert "## Skipped" in md and "dir missing" in md
    assert "## NEEDS-REVIEW" in md and "裸断言" in md
    assert "## Overall Verdict" in md and "整体可" in md
    assert "abc1234" in md
