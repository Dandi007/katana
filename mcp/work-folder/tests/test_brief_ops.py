"""Flat ``wf-ID`` brief seed/touch tests."""

from pathlib import Path

import pytest

from katana_work_folder_mcp.brief import BRIEF_NAME, parse_brief
from katana_work_folder_mcp.brief_ops import main, seed_brief, touch_brief


def _folder(tmp_path: Path, folder_id: str = "wf-abc123") -> Path:
    folder = tmp_path / folder_id
    folder.mkdir()
    return folder


def test_seed_brief_uses_explicit_folder_id(tmp_path):
    folder = _folder(tmp_path)
    assert seed_brief(
        str(folder),
        folder_id="wf-abc123",
        title="Demo",
        goal="Goal",
        now="2026-07-01",
    )
    parsed = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert parsed["frontmatter"]["id"] == "wf-abc123"
    assert parsed["frontmatter"]["created"] == "2026-07-01"
    assert parsed["goal"] == "Goal"


def test_seed_brief_rejects_noncanonical_id(tmp_path):
    folder = _folder(tmp_path, "legacy-name")
    with pytest.raises(ValueError, match="invalid folder_id"):
        seed_brief(
            str(folder),
            folder_id="legacy-name",
            title="Demo",
            goal="Goal",
            now="2026-07-01",
        )


def test_seed_brief_is_idempotent(tmp_path):
    folder = _folder(tmp_path)
    seed_brief(
        str(folder),
        folder_id="wf-abc123",
        title="First",
        goal="g1",
        now="2026-07-01",
    )
    assert not seed_brief(
        str(folder),
        folder_id="wf-abc123",
        title="Second",
        goal="g2",
        now="2026-07-02",
    )
    parsed = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert parsed["frontmatter"]["title"] == "First"
    assert parsed["goal"] == "g1"


def test_touch_updates_and_reactivates(tmp_path):
    folder = _folder(tmp_path)
    seed_brief(
        str(folder),
        folder_id="wf-abc123",
        title="T",
        goal="g",
        status="paused",
        now="2026-07-01",
    )
    assert touch_brief(
        str(folder),
        folder_id="wf-abc123",
        now="2026-07-05",
    )
    parsed = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert parsed["frontmatter"]["updated"] == "2026-07-05"
    assert parsed["frontmatter"]["status"] == "active"


def test_touch_preserves_completed_status(tmp_path):
    folder = _folder(tmp_path)
    seed_brief(
        str(folder),
        folder_id="wf-abc123",
        title="T",
        goal="g",
        status="completed",
        now="2026-07-01",
    )
    touch_brief(
        str(folder),
        folder_id="wf-abc123",
        now="2026-07-05",
    )
    parsed = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert parsed["frontmatter"]["status"] == "completed"


def test_touch_seeds_with_explicit_identity(tmp_path):
    folder = _folder(tmp_path)
    assert touch_brief(
        str(folder),
        folder_id="wf-abc123",
        now="2026-07-05",
        seed_title="Title",
        seed_goal="Goal",
    )
    parsed = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert parsed["frontmatter"]["id"] == "wf-abc123"


def test_touch_missing_without_seed_is_noop(tmp_path):
    folder = _folder(tmp_path)
    assert not touch_brief(
        str(folder),
        folder_id="wf-abc123",
        now="2026-07-05",
    )


def test_touch_rejects_brief_directory_mismatch(tmp_path):
    folder = _folder(tmp_path)
    seed_brief(
        str(folder),
        folder_id="wf-abc123",
        title="T",
        goal="g",
        now="2026-07-01",
    )
    with pytest.raises(ValueError, match="does not match"):
        touch_brief(
            str(folder),
            folder_id="wf-ffffff",
            now="2026-07-05",
        )


def test_touch_corrupt_brief_is_noop(tmp_path):
    folder = _folder(tmp_path)
    (folder / BRIEF_NAME).write_text("not frontmatter", encoding="utf-8")
    assert not touch_brief(
        str(folder),
        folder_id="wf-abc123",
        now="2026-07-05",
    )


def test_cli_uses_flat_folder_basename_as_id(tmp_path):
    folder = _folder(tmp_path)
    seed_brief(
        str(folder),
        folder_id="wf-abc123",
        title="T",
        goal="g",
        status="paused",
        now="2026-07-01",
    )
    assert main([str(folder), "--date", "2026-07-09"]) == 0
    parsed = parse_brief((folder / BRIEF_NAME).read_text(encoding="utf-8"))
    assert parsed["frontmatter"]["updated"] == "2026-07-09"
    assert parsed["frontmatter"]["status"] == "active"
