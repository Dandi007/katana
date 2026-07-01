"""test_lifecycle_brief.py — lifecycle 的 create/save/resume/list 对 _brief.md 的维护。"""
from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from katana_work_folder_mcp import lifecycle as lc
from katana_work_folder_mcp.brief import BRIEF_NAME, parse_brief


class _Clock:
    def __init__(self, dt: datetime.datetime):
        self.dt = dt

    def __call__(self) -> datetime.datetime:
        return self.dt


def _now(y=2026, m=7, d=1, hh=10, mm=0):
    return _Clock(datetime.datetime(y, m, d, hh, mm))


def _brief_fm(folder: Path) -> dict:
    return parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# create → seed brief
# ---------------------------------------------------------------------------

def test_create_seeds_brief(tmp_path: Path):
    r = lc.do_create(str(tmp_path), "Demo 主题 X", now_fn=_now())
    folder = Path(r["path"])
    assert (folder / BRIEF_NAME).exists()
    assert BRIEF_NAME in r["seeded"]
    fm = _brief_fm(folder)["frontmatter"]
    assert fm["status"] == "active"
    assert fm["id"].startswith("2026-0701-")
    assert fm["created"] == "2026-07-01"


# ---------------------------------------------------------------------------
# save → touch brief (bump updated + reactivate)
# ---------------------------------------------------------------------------

def test_save_touches_brief_updated(tmp_path: Path):
    created = lc.do_create(str(tmp_path), "主题", now_fn=_now(d=1))
    folder = created["path"]
    # 手动把 brief 改成 paused，验证 save 复活
    bp = Path(folder) / BRIEF_NAME
    bp.write_text(bp.read_text().replace("status: active", "status: paused"), encoding="utf-8")

    saved = lc.do_save(folder, now_fn=_now(d=5), summary="推进了一步")
    assert BRIEF_NAME in saved["written"]
    fm = _brief_fm(Path(folder))["frontmatter"]
    assert fm["updated"] == "2026-07-05"
    assert fm["status"] == "active"


def test_save_seeds_brief_when_missing(tmp_path: Path):
    # 老 folder 没有 brief：save 应 best-effort seed（用 progress 的 goal）
    folder = tmp_path / "2026" / "07" / "01" / "legacy"
    folder.mkdir(parents=True)
    (folder / "progress.md").write_text(
        "# Progress\n\n**Goal:** 老目标\n**Status:** active\n\n## Changelog\n"
        "| Time | Action | Detail |\n|------|--------|--------|\n",
        encoding="utf-8",
    )
    saved = lc.do_save(str(folder), now_fn=_now(d=5), summary="cp")
    assert (folder / BRIEF_NAME).exists()
    assert BRIEF_NAME in saved["written"]
    r = _brief_fm(folder)
    assert r["goal"] == "老目标"
    assert r["frontmatter"]["status"] == "active"


# ---------------------------------------------------------------------------
# resume → touch brief (reactivate on write, D8)
# ---------------------------------------------------------------------------

def test_resume_reactivates_brief(tmp_path: Path):
    created = lc.do_create(str(tmp_path), "主题", now_fn=_now(d=1))
    folder = created["path"]
    bp = Path(folder) / BRIEF_NAME
    bp.write_text(bp.read_text().replace("status: active", "status: archived"), encoding="utf-8")

    res = lc.do_resume(folder, now_fn=_now(d=9))
    assert res["ok"] is True
    fm = _brief_fm(Path(folder))["frontmatter"]
    assert fm["status"] == "active"
    assert fm["updated"] == "2026-07-09"


# ---------------------------------------------------------------------------
# list → enrich with brief fields
# ---------------------------------------------------------------------------

def test_list_enriches_with_brief(tmp_path: Path):
    created = lc.do_create(str(tmp_path), "可列出的主题", now_fn=_now(d=1))
    out = lc.do_list(str(tmp_path))
    cands = out["candidates"]
    assert cands, "应至少列出刚创建的 folder"
    hit = next(c for c in cands if c["path"] == created["path"])
    assert hit.get("title") == "可列出的主题"
    assert hit.get("goal") == "可列出的主题"
    assert hit.get("brief_status") == "active"
    assert hit.get("updated") == "2026-07-01"
