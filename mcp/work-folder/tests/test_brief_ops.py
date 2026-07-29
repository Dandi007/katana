"""Legacy physical-path brief writers fail closed after the flat cutover."""

from pathlib import Path

import pytest

from katana_work_folder_mcp.brief_ops import (
    DirectMutationRetiredError,
    main,
    seed_brief,
    touch_brief,
)


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "wf-abc123"
    folder.mkdir()
    return folder


def test_seed_brief_is_retired_without_writing(tmp_path: Path) -> None:
    folder = _folder(tmp_path)

    with pytest.raises(DirectMutationRetiredError, match="MCP"):
        seed_brief(
            str(folder),
            folder_id="wf-abc123",
            title="Demo",
            goal="Goal",
            now="2026-07-01",
        )

    assert list(folder.iterdir()) == []


def test_touch_brief_is_retired_without_writing(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    brief = folder / "_brief.md"
    brief.write_text("unchanged\n", encoding="utf-8")

    with pytest.raises(DirectMutationRetiredError, match="folder_id"):
        touch_brief(
            str(folder),
            folder_id="wf-abc123",
            now="2026-07-05",
        )

    assert brief.read_text(encoding="utf-8") == "unchanged\n"


def test_retired_cli_returns_usage_error_without_mutation(
    tmp_path: Path,
) -> None:
    folder = _folder(tmp_path)

    assert main([str(folder)]) == 2
    assert list(folder.iterdir()) == []
