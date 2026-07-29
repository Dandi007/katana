"""Flat Work Folder lifecycle 的只读契约测试。"""

from pathlib import Path

import pytest

from katana_work_folder_mcp.brief import render_brief
from katana_work_folder_mcp.lifecycle import (
    RESUME_BLOCKED_CONTRACT,
    RESUME_PROCEED_CONTRACT,
    SAVE_CONTRACT,
    do_list,
    require_folder_id,
)


def _make_folder(
    root: Path,
    folder_id: str,
    *,
    status: str = "execution",
    title: str | None = None,
) -> Path:
    folder = root / folder_id
    folder.mkdir()
    (folder / "progress.md").write_text(
        f"# Progress\n\n**Status:** {status}\n",
        encoding="utf-8",
    )
    if title is not None:
        (folder / "_brief.md").write_text(
            render_brief(
                id=folder_id,
                title=title,
                status="active",
                created="2026-07-01",
                updated="2026-07-02",
                goal=f"{title} goal",
                summary="",
            ),
            encoding="utf-8",
        )
    return folder


@pytest.mark.parametrize("folder_id", ["wf-000001", "wf-abcdef", "wf-9a0b1c"])
def test_require_folder_id_accepts_canonical_ids(folder_id):
    assert require_folder_id(folder_id) == folder_id


@pytest.mark.parametrize(
    "folder_id",
    [
        "",
        "wf-12345",
        "wf-1234567",
        "wf-ABCDEF",
        "2026-0701-topic",
        "../wf-000001",
        "wf-000001/progress.md",
        None,
    ],
)
def test_require_folder_id_rejects_noncanonical_values(folder_id):
    with pytest.raises(ValueError, match="invalid folder_id"):
        require_folder_id(folder_id)


def test_public_agent_contracts_remain_available():
    assert "Save 判断契约" in SAVE_CONTRACT
    assert "Resume 继续契约" in RESUME_PROCEED_CONTRACT
    assert "Resume 阻塞契约" in RESUME_BLOCKED_CONTRACT


def test_do_list_returns_only_flat_active_folder_ids(tmp_path):
    _make_folder(tmp_path, "wf-000001", title="Active")
    _make_folder(tmp_path, "wf-000002", status="completed", title="Done")
    nested = tmp_path / "2026" / "07" / "01" / "legacy"
    nested.mkdir(parents=True)
    (nested / "progress.md").write_text(
        "# Progress\n\n**Status:** execution\n",
        encoding="utf-8",
    )

    result = do_list(str(tmp_path))

    assert [item["folder_id"] for item in result["candidates"]] == ["wf-000001"]
    assert all("path" not in item for item in result["candidates"])


def test_do_list_enriches_brief_without_exposing_path(tmp_path):
    _make_folder(tmp_path, "wf-000001", title="可列出的主题")

    [candidate] = do_list(str(tmp_path))["candidates"]

    assert candidate["folder_id"] == "wf-000001"
    assert candidate["title"] == "可列出的主题"
    assert candidate["goal"] == "可列出的主题 goal"
    assert candidate["brief_status"] == "active"
    assert candidate["updated"] == "2026-07-02"
    assert "path" not in candidate


def test_do_list_ignores_mismatched_brief_identity(tmp_path):
    folder = _make_folder(tmp_path, "wf-000001")
    (folder / "_brief.md").write_text(
        render_brief(
            id="wf-000002",
            title="Wrong",
            status="active",
            created="2026-07-01",
            updated="2026-07-02",
            goal="wrong",
            summary="",
        ),
        encoding="utf-8",
    )

    [candidate] = do_list(str(tmp_path))["candidates"]

    assert candidate["folder_id"] == "wf-000001"
    assert "title" not in candidate


def test_do_list_respects_limit_and_missing_root(tmp_path):
    for index in range(4):
        _make_folder(tmp_path, f"wf-{index:06x}")

    assert len(do_list(str(tmp_path), limit=2)["candidates"]) == 2
    assert do_list(str(tmp_path / "missing")) == {"candidates": []}
