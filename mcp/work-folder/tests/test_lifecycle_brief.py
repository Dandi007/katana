"""Lifecycle brief enrichment 的 flat identity 边界测试。"""

from pathlib import Path

from katana_work_folder_mcp.brief import render_brief
from katana_work_folder_mcp.lifecycle import do_list


def _seed(root: Path, folder_id: str, brief: str) -> Path:
    folder = root / folder_id
    folder.mkdir()
    (folder / "progress.md").write_text(
        "# Progress\n\n**Status:** execution\n",
        encoding="utf-8",
    )
    (folder / "_brief.md").write_text(brief, encoding="utf-8")
    return folder


def test_list_skips_corrupt_brief_enrichment(tmp_path):
    _seed(tmp_path, "wf-000001", "not frontmatter")

    [candidate] = do_list(str(tmp_path))["candidates"]

    assert candidate["folder_id"] == "wf-000001"
    assert "title" not in candidate


def test_list_serializes_yaml_date_as_iso_string(tmp_path):
    _seed(
        tmp_path,
        "wf-000001",
        render_brief(
            id="wf-000001",
            title="主题",
            status="active",
            created="2026-07-01",
            updated="2026-07-09",
            goal="目标",
            summary="",
        ),
    )

    [candidate] = do_list(str(tmp_path))["candidates"]

    assert candidate["updated"] == "2026-07-09"
    assert candidate["goal"] == "目标"


def test_list_never_returns_brief_file_location(tmp_path):
    _seed(
        tmp_path,
        "wf-000001",
        render_brief(
            id="wf-000001",
            title="主题",
            status="active",
            created="2026-07-01",
            updated="2026-07-09",
            goal="目标",
            summary="",
        ),
    )

    [candidate] = do_list(str(tmp_path))["candidates"]

    assert not {"path", "folder", "wf_abs", "absolute_path"} & candidate.keys()
