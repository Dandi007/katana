"""test_brief_ops.py — brief_ops 的 folder 级 seed/touch 维护操作测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from katana_work_folder_mcp.brief import BRIEF_NAME, parse_brief
from katana_work_folder_mcp.brief_ops import derive_id, seed_brief, touch_brief


# ---------------------------------------------------------------------------
# derive_id
# ---------------------------------------------------------------------------

def test_derive_id_from_dated_path():
    folder = "/x/智元工作/工作记录/2026/07/01/work-folder-系统重构设计"
    assert derive_id(folder) == "2026-0701-work-folder-系统重构设计"


def test_derive_id_fallback_when_not_dated():
    # 非 YYYY/MM/DD 布局 → 退化为 slug（末段），不崩
    assert derive_id("/tmp/random-topic") == "random-topic"


# ---------------------------------------------------------------------------
# seed_brief
# ---------------------------------------------------------------------------

def test_seed_brief_creates_when_missing(tmp_path: Path):
    folder = tmp_path / "2026" / "07" / "01" / "demo-topic"
    folder.mkdir(parents=True)
    created = seed_brief(str(folder), title="Demo 主题", goal="把 X 做成 Y",
                         status="active", now="2026-07-01")
    assert created is True
    brief = folder / BRIEF_NAME
    assert brief.exists()
    r = parse_brief(brief.read_text(encoding="utf-8"))
    fm = r["frontmatter"]
    assert fm["id"] == "2026-0701-demo-topic"
    assert fm["title"] == "Demo 主题"
    assert fm["status"] == "active"
    assert fm["created"] == "2026-07-01"
    assert fm["updated"] == "2026-07-01"
    assert r["goal"] == "把 X 做成 Y"


def test_seed_brief_noop_when_exists(tmp_path: Path):
    folder = tmp_path / "2026" / "07" / "01" / "demo-topic"
    folder.mkdir(parents=True)
    seed_brief(str(folder), title="第一次", goal="g1", status="active", now="2026-07-01")
    # 第二次不覆盖
    created = seed_brief(str(folder), title="第二次", goal="g2", status="active", now="2026-07-02")
    assert created is False
    r = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert r["frontmatter"]["title"] == "第一次"
    assert r["goal"] == "g1"


# ---------------------------------------------------------------------------
# touch_brief
# ---------------------------------------------------------------------------

def test_touch_brief_bumps_updated_and_reactivates(tmp_path: Path):
    folder = tmp_path / "2026" / "07" / "01" / "demo-topic"
    folder.mkdir(parents=True)
    seed_brief(str(folder), title="T", goal="g", status="paused", now="2026-07-01")
    # 手动改成 paused 以验证 reactivate
    brief = folder / BRIEF_NAME
    text = brief.read_text(encoding="utf-8").replace("status: active", "status: paused")
    brief.write_text(text, encoding="utf-8")

    touched = touch_brief(str(folder), now="2026-07-05", reactivate=True)
    assert touched is True
    r = parse_brief(brief.read_text(encoding="utf-8"))
    assert r["frontmatter"]["updated"] == "2026-07-05"
    assert r["frontmatter"]["status"] == "active"
    # 保留 goal / title / created
    assert r["goal"] == "g"
    assert r["frontmatter"]["title"] == "T"
    assert r["frontmatter"]["created"] == "2026-07-01"


def test_touch_brief_preserves_status_when_no_reactivate(tmp_path: Path):
    folder = tmp_path / "2026" / "07" / "01" / "demo-topic"
    folder.mkdir(parents=True)
    seed_brief(str(folder), title="T", goal="g", status="active", now="2026-07-01")
    brief = folder / BRIEF_NAME
    brief.write_text(brief.read_text(encoding="utf-8").replace("status: active", "status: completed"),
                     encoding="utf-8")
    touch_brief(str(folder), now="2026-07-05", reactivate=False)
    r = parse_brief(brief.read_text(encoding="utf-8"))
    assert r["frontmatter"]["status"] == "completed"
    assert r["frontmatter"]["updated"] == "2026-07-05"


def test_touch_brief_seeds_when_missing(tmp_path: Path):
    # touch 一个还没 brief 的 folder：应 best-effort seed（用 progress goal 兜底给的 title/goal）
    folder = tmp_path / "2026" / "07" / "01" / "no-brief-topic"
    folder.mkdir(parents=True)
    touched = touch_brief(str(folder), now="2026-07-05", reactivate=True,
                          seed_title="兜底标题", seed_goal="兜底目标")
    assert touched is True
    r = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert r["frontmatter"]["status"] == "active"
    assert r["frontmatter"]["title"] == "兜底标题"
    assert r["frontmatter"]["updated"] == "2026-07-05"


def test_touch_brief_noop_when_missing_and_no_seed(tmp_path: Path):
    folder = tmp_path / "2026" / "07" / "01" / "no-brief-topic"
    folder.mkdir(parents=True)
    touched = touch_brief(str(folder), now="2026-07-05", reactivate=True)
    assert touched is False
    assert not (folder / BRIEF_NAME).exists()


def test_touch_brief_survives_corrupt_brief(tmp_path: Path):
    # brief 无 frontmatter → touch 不崩，返回 False（不吞不改）
    folder = tmp_path / "2026" / "07" / "01" / "bad-topic"
    folder.mkdir(parents=True)
    (folder / BRIEF_NAME).write_text("no frontmatter here", encoding="utf-8")
    touched = touch_brief(str(folder), now="2026-07-05", reactivate=True)
    assert touched is False


# ---------------------------------------------------------------------------
# wf-touch CLI
# ---------------------------------------------------------------------------

def test_cli_wf_touch_updates_date(tmp_path: Path):
    from katana_work_folder_mcp.brief_ops import main
    folder = tmp_path / "2026" / "07" / "01" / "cli-topic"
    folder.mkdir(parents=True)
    seed_brief(str(folder), title="T", goal="g", status="paused", now="2026-07-01")
    brief = folder / BRIEF_NAME
    brief.write_text(brief.read_text(encoding="utf-8").replace("status: active", "status: paused"),
                     encoding="utf-8")
    rc = main([str(folder), "--date", "2026-07-09"])
    assert rc == 0
    r = parse_brief(brief.read_text(encoding="utf-8"))
    assert r["frontmatter"]["updated"] == "2026-07-09"
    assert r["frontmatter"]["status"] == "active"


def test_cli_wf_touch_no_reactivate(tmp_path: Path):
    from katana_work_folder_mcp.brief_ops import main
    folder = tmp_path / "2026" / "07" / "01" / "cli-topic2"
    folder.mkdir(parents=True)
    seed_brief(str(folder), title="T", goal="g", status="paused", now="2026-07-01")
    brief = folder / BRIEF_NAME
    brief.write_text(brief.read_text(encoding="utf-8").replace("status: active", "status: paused"),
                     encoding="utf-8")
    main([str(folder), "--date", "2026-07-09", "--no-reactivate"])
    r = parse_brief(brief.read_text(encoding="utf-8"))
    assert r["frontmatter"]["status"] == "paused"
