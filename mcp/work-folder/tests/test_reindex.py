"""test_reindex.py — wf-reindex 扫全库 _brief.md 生成 INDEX.md 的单测。"""
import pytest

from katana_work_folder_mcp.brief import BRIEF_NAME, render_brief
from katana_work_folder_mcp.reindex import (
    collect_briefs,
    reindex,
    render_index,
)


def _brief(id, title, status, updated, goal="做 X", created="2026-02-17"):
    return render_brief(
        id=id, title=title, status=status, created=created, updated=updated,
        goal=goal, summary="摘要。",
    )


# --- collect_briefs ------------------------------------------------------

def test_collect_briefs_finds_nested_briefs(tmp_path):
    # YYYY/MM/DD/<slug>/_brief.md 嵌套布局
    d1 = tmp_path / "2026/02/11/foo"
    d2 = tmp_path / "2026/06/30/bar"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / BRIEF_NAME).write_text(_brief("2026-0211-foo", "Foo", "completed", "2026-02-11"), encoding="utf-8")
    (d2 / BRIEF_NAME).write_text(_brief("2026-0630-bar", "Bar", "active", "2026-06-30"), encoding="utf-8")

    entries = collect_briefs(str(tmp_path))
    ids = sorted(e["fm"]["id"] for e in entries)
    assert ids == ["2026-0211-foo", "2026-0630-bar"]
    assert all("folder" in e and "goal" in e for e in entries)


def test_collect_briefs_skips_unparseable(tmp_path):
    d = tmp_path / "2026/02/11/bad"
    d.mkdir(parents=True)
    (d / BRIEF_NAME).write_text("没有 frontmatter 的正文", encoding="utf-8")
    entries, errors = collect_briefs(str(tmp_path), return_errors=True)
    assert entries == []
    assert len(errors) == 1
    assert "bad" in errors[0]


# --- render_index --------------------------------------------------------

def test_render_index_sorts_by_updated_desc():
    entries = [
        {"folder": "/a", "fm": {"id": "old", "title": "旧", "status": "completed", "updated": "2026-02-11"}, "goal": "g1"},
        {"folder": "/b", "fm": {"id": "new", "title": "新", "status": "active", "updated": "2026-07-01"}, "goal": "g2"},
        {"folder": "/c", "fm": {"id": "mid", "title": "中", "status": "paused", "updated": "2026-06-30"}, "goal": "g3"},
    ]
    md = render_index(entries)
    # 倒序：new(07-01) → mid(06-30) → old(02-11)
    pos_new = md.find("新")
    pos_mid = md.find("中")
    pos_old = md.find("旧")
    assert pos_new < pos_mid < pos_old
    # 表头含关键字段
    assert "updated" in md and "status" in md and "id" in md


def test_render_index_handles_empty():
    md = render_index([])
    assert "INDEX" in md  # 仍有表头


# --- reindex（集成）------------------------------------------------------

def test_reindex_writes_index_md(tmp_path):
    d1 = tmp_path / "2026/02/11/foo"
    d2 = tmp_path / "2026/06/30/bar"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / BRIEF_NAME).write_text(_brief("2026-0211-foo", "Foo", "completed", "2026-02-11"), encoding="utf-8")
    (d2 / BRIEF_NAME).write_text(_brief("2026-0630-bar", "Bar", "active", "2026-06-30"), encoding="utf-8")

    r = reindex(str(tmp_path))
    assert r["indexed"] == 2
    assert r["skipped"] == 0
    idx = (tmp_path / "INDEX.md")
    assert idx.exists()
    content = idx.read_text(encoding="utf-8")
    # 倒序：bar 在 foo 前
    assert content.find("Bar") < content.find("Foo")


def test_reindex_dry_run_does_not_write(tmp_path):
    d = tmp_path / "2026/02/11/foo"
    d.mkdir(parents=True)
    (d / BRIEF_NAME).write_text(_brief("2026-0211-foo", "Foo", "active", "2026-02-11"), encoding="utf-8")

    r = reindex(str(tmp_path), dry_run=True)
    assert r["indexed"] == 1
    assert not (tmp_path / "INDEX.md").exists()
    assert "preview" in r  # dry-run 返回预览文本


def test_reindex_skips_folders_without_brief(tmp_path):
    d1 = tmp_path / "2026/02/11/has"
    d2 = tmp_path / "2026/02/12/none"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / BRIEF_NAME).write_text(_brief("2026-0211-has", "Has", "active", "2026-02-11"), encoding="utf-8")
    (d2 / "progress.md").write_text("# Progress", encoding="utf-8")  # 无 brief

    r = reindex(str(tmp_path))
    assert r["indexed"] == 1
    assert r["skipped"] == 1
