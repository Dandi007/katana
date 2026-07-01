"""test_brief.py — _brief.md schema 的 parse/render/validate/lint 单测。"""
import pytest

from katana_work_folder_mcp.brief import (
    BRIEF_NAME,
    BriefError,
    lint_folder,
    parse_brief,
    render_brief,
    validate_brief,
)

SAMPLE = """---
id: 2026-0217-demo
title: Demo 工作
status: active
created: 2026-02-17
updated: 2026-07-01
tags: [loop-engine, mcp]
kind: design
links: ["[[other-folder]]"]
---

**Goal:** 把 X 做成 Y

这是一段摘要，说明现在到哪。
"""


# --- parse ---------------------------------------------------------------

def test_parse_brief_splits_frontmatter_goal_summary():
    r = parse_brief(SAMPLE)
    assert r["frontmatter"]["id"] == "2026-0217-demo"
    assert r["frontmatter"]["status"] == "active"
    assert r["frontmatter"]["tags"] == ["loop-engine", "mcp"]
    assert r["goal"] == "把 X 做成 Y"
    assert "现在到哪" in r["summary"]


def test_parse_brief_no_frontmatter_raises():
    with pytest.raises(BriefError):
        parse_brief("没有 frontmatter 的正文")


# --- render round-trip ---------------------------------------------------

def test_render_then_parse_roundtrip():
    text = render_brief(
        id="2026-0217-demo", title="Demo 工作", status="active",
        created="2026-02-17", updated="2026-07-01",
        goal="把 X 做成 Y", summary="现在到哪。",
        tags=["a", "b"], kind="design", links=["[[x]]"],
    )
    r = parse_brief(text)
    assert r["frontmatter"]["title"] == "Demo 工作"
    assert r["frontmatter"]["tags"] == ["a", "b"]
    assert r["goal"] == "把 X 做成 Y"
    assert r["summary"].strip() == "现在到哪。"


# --- validate ------------------------------------------------------------

def _ok_text():
    return render_brief(id="i", title="t", status="active",
                        created="2026-02-17", updated="2026-07-01",
                        goal="g", summary="s")


def test_validate_ok():
    assert validate_brief(_ok_text()) == []


def test_validate_missing_field_and_bad_status_and_empty_goal():
    bad = render_brief(id="i", title="t", status="wrong",
                       created="2026-02-17", updated="2026-07-01",
                       goal="", summary="s")
    problems = validate_brief(bad)
    assert any("status" in p for p in problems)
    assert any("Goal" in p for p in problems)


# --- lint_folder ---------------------------------------------------------

def test_lint_folder_flags_missing_brief_and_progress(tmp_path):
    r = lint_folder(str(tmp_path))
    assert r["ok"] is False
    assert any("_brief.md" in p for p in r["problems"])
    assert any("progress.md" in p for p in r["problems"])


def test_lint_folder_ok(tmp_path):
    (tmp_path / BRIEF_NAME).write_text(_ok_text(), encoding="utf-8")
    (tmp_path / "progress.md").write_text("# Progress\n", encoding="utf-8")
    r = lint_folder(str(tmp_path))
    assert r["ok"] is True and r["problems"] == []
