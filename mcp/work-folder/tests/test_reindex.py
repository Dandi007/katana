"""Flat ``wf-ID/`` reindex contract tests."""

from katana_work_folder_mcp.brief import BRIEF_NAME, render_brief
from katana_work_folder_mcp.reindex import collect_briefs, reindex, render_index


def _brief(folder_id, title, status, updated, goal="做 X", created="2026-02-17"):
    return render_brief(
        id=folder_id,
        title=title,
        status=status,
        created=created,
        updated=updated,
        goal=goal,
        summary="摘要。",
    )


def _seed(root, folder_id, *, title="主题", status="active", updated="2026-07-01"):
    folder = root / folder_id
    folder.mkdir()
    (folder / BRIEF_NAME).write_text(
        _brief(folder_id, title, status, updated),
        encoding="utf-8",
    )
    return folder


def test_collect_briefs_finds_only_direct_flat_folders(tmp_path):
    _seed(tmp_path, "wf-000001", title="Foo", status="completed", updated="2026-02-11")
    _seed(tmp_path, "wf-000002", title="Bar", updated="2026-06-30")
    legacy = tmp_path / "2026" / "06" / "30" / "bar"
    legacy.mkdir(parents=True)
    (legacy / BRIEF_NAME).write_text(
        _brief("wf-000003", "Legacy", "active", "2026-06-30"),
        encoding="utf-8",
    )

    entries = collect_briefs(str(tmp_path))

    assert sorted(entry["folder_id"] for entry in entries) == [
        "wf-000001",
        "wf-000002",
    ]
    assert all("folder" not in entry and "path" not in entry for entry in entries)


def test_collect_briefs_rejects_corrupt_and_identity_mismatch(tmp_path):
    corrupt = tmp_path / "wf-000001"
    corrupt.mkdir()
    (corrupt / BRIEF_NAME).write_text("没有 frontmatter", encoding="utf-8")
    mismatch = tmp_path / "wf-000002"
    mismatch.mkdir()
    (mismatch / BRIEF_NAME).write_text(
        _brief("wf-000003", "Wrong", "active", "2026-07-01"),
        encoding="utf-8",
    )

    entries, errors = collect_briefs(str(tmp_path), return_errors=True)

    assert entries == []
    assert len(errors) == 2
    assert any("wf-000001/_brief.md" in error for error in errors)
    assert any("id mismatch" in error for error in errors)


def test_render_index_sorts_by_updated_desc_and_has_no_location_column():
    entries = [
        {
            "folder_id": "wf-000001",
            "fm": {
                "id": "wf-000001",
                "title": "旧",
                "status": "completed",
                "updated": "2026-02-11",
            },
            "goal": "g1",
        },
        {
            "folder_id": "wf-000002",
            "fm": {
                "id": "wf-000002",
                "title": "新",
                "status": "active",
                "updated": "2026-07-01",
            },
            "goal": "g2",
        },
    ]

    md = render_index(entries)

    assert md.find("新") < md.find("旧")
    assert "| updated | status | id | title | goal |" in md
    assert "folder |" not in md
    assert "path |" not in md


def test_render_index_handles_empty():
    assert "Work Folder INDEX" in render_index([])


def test_reindex_write_mode_is_retired_without_writing(tmp_path):
    _seed(tmp_path, "wf-000001", title="Foo", updated="2026-02-11")
    _seed(tmp_path, "wf-000002", title="Bar", updated="2026-06-30")

    import pytest

    with pytest.raises(RuntimeError, match="wf_reindex"):
        reindex(str(tmp_path), dry_run=False)

    assert not (tmp_path / "INDEX.md").exists()


def test_reindex_dry_run_returns_preview_without_writing(tmp_path):
    _seed(tmp_path, "wf-000001", title="Foo")

    result = reindex(str(tmp_path), dry_run=True)

    assert result["indexed"] == 1
    assert "preview" in result
    assert not (tmp_path / "INDEX.md").exists()


def test_reindex_counts_flat_progress_folder_without_brief_as_skipped(tmp_path):
    _seed(tmp_path, "wf-000001")
    missing = tmp_path / "wf-000002"
    missing.mkdir()
    (missing / "progress.md").write_text("# Progress", encoding="utf-8")
    invalid_name = tmp_path / "topic"
    invalid_name.mkdir()
    (invalid_name / "progress.md").write_text("# Progress", encoding="utf-8")

    result = reindex(str(tmp_path), dry_run=True)

    assert result["indexed"] == 1
    assert result["skipped"] == 1
