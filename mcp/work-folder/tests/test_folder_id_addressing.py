"""Opaque folder ID addressing makes path-based ghost nesting impossible."""

from __future__ import annotations

import inspect
import subprocess
from datetime import datetime

import pytest

from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    TransactionManifest,
)
from katana_work_folder_mcp.fs_tools import FSTools
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


def _now():
    return datetime(2026, 7, 29, 16, 0, 0)


@pytest.fixture
def flat(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

    kernel = GovernedKernel()
    vfs = GovernedVFS(str(tmp_path))
    ledger = ResourceIdLedger(
        str(tmp_path / ".katana" / "tombstones.json"),
        prefix="wf-",
    )
    manifest = TransactionManifest(str(tmp_path / ".katana" / "manifests"))
    kernel.bind("work-folder", _wf_policy(), vfs, ledger, manifest, str(tmp_path))
    store = WorkFolderStore(kernel)
    tools = FSTools(kernel, str(tmp_path))
    folder_id = store.create("addressing", _now)["folder_id"]
    return tmp_path, folder_id, tools


def test_create_by_id_writes_only_inside_flat_folder(flat):
    repo, folder_id, tools = flat

    result = tools.fs_create(folder_id, "design.md", "# Design\n")

    assert result["ok"] is True
    assert result["folder_id"] == folder_id
    assert result["filename"] == "design.md"
    assert (repo / folder_id / "design.md").is_file()
    assert list(repo.rglob("design.md")) == [repo / folder_id / "design.md"]


def test_write_by_id_never_accepts_physical_locator(flat):
    repo, folder_id, tools = flat
    tools.fs_create(folder_id, "findings.md", "init\n")

    result = tools.fs_write(folder_id, "findings.md", "# Findings\n")

    assert result["ok"] is True
    assert (repo / folder_id / "findings.md").read_text(encoding="utf-8") == "# Findings\n"
    assert not (repo / "智元工作").exists()


def test_missing_folder_id_is_not_resolved_by_scan(flat):
    _, _, tools = flat

    result = tools.fs_create("wf-deadbe", "design.md", "# Design\n")

    assert result["ok"] is False
    assert result["code"] == "RESOURCE_NOT_FOUND"
    assert result["folder_id"] == "wf-deadbe"


@pytest.mark.parametrize(
    "folder_id",
    ["2026/07/29/topic", "../wf-abc123", "/abs/path", "wf-ABCDEF"],
)
def test_legacy_and_path_like_folder_ids_are_rejected(flat, folder_id):
    _, _, tools = flat

    result = tools.fs_read(folder_id, "progress.md")

    assert result["ok"] is False
    assert result["code"] == "INVALID_PATH"


def test_old_optional_folder_id_call_shape_no_longer_exists():
    assert list(inspect.signature(FSTools.fs_create).parameters)[:4] == [
        "self",
        "folder_id",
        "filename",
        "content",
    ]
    assert list(inspect.signature(FSTools.fs_write).parameters)[:4] == [
        "self",
        "folder_id",
        "filename",
        "content",
    ]
    with pytest.raises(TypeError):
        FSTools.fs_create(object(), "design.md", "# Design\n")
