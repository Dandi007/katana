"""test_report.py — wf-report 汇总 workflow JSON + git status 生成 review 报告。"""
import json

from katana_work_folder_mcp.report import (
    render_report,
    summarize_report,
)


def _sample_report():
    """模拟 backfill.workflow.js 返回的 JSON。"""
    return {
        "classified": 5,
        "normalized": 3,
        "explored": 2,
        "failed": 1,
        "reports": [
            {"folder": "/a", "created": ["_brief.md"], "moved": ["errors.md"], "skipped": False, "note": "ok"},
            {"folder": "/b", "created": ["_brief.md"], "moved": [], "skipped": False, "note": "ok"},
            {"folder": "/c", "created": ["_brief.md", "artifacts/"], "moved": ["task_plan.md"], "skipped": False, "note": "ok"},
            {"folder": "/d", "created": [], "moved": [], "skipped": True, "note": "已有合规 brief"},
            {"folder": "/e", "created": [], "moved": [], "skipped": False, "note": "ERROR: parse failed"},
        ],
    }


# --- summarize_report ----------------------------------------------------

def test_summarize_counts_created_moved_skipped_failed():
    s = summarize_report(_sample_report())
    assert s["folders"] == 5
    assert s["created_total"] == 4       # a:1 + b:1 + c:2
    assert s["moved_total"] == 2         # a:1 + c:1
    assert s["skipped"] == 1
    assert s["failed"] == 1
    assert s["normalized"] == 3          # 非 skip 非 fail 的改动
    assert s["explored"] == 2


def test_summarize_handles_empty():
    s = summarize_report({"reports": []})
    assert s["folders"] == 0
    assert s["created_total"] == 0


def test_summarize_collects_errors():
    s = summarize_report(_sample_report())
    assert any("e" in err for err in s["errors"])


# --- render_report -------------------------------------------------------

def test_render_report_includes_stats_and_git_changes():
    s = summarize_report(_sample_report())
    git_status = [
        "?? 智元工作/工作记录/2026/02/11/foo/_brief.md",
        "R  智元工作/工作记录/2026/02/11/foo/errors.md -> .../artifacts/errors.md",
    ]
    md = render_report(s, git_status)
    assert "folders" in md and str(s["folders"]) in md
    assert "created" in md.lower()
    assert "_brief.md" in md
    assert "artifacts" in md


def test_render_report_handles_no_git_changes():
    s = summarize_report(_sample_report())
    md = render_report(s, [])
    assert "无 git 改动" in md or "0" in md
