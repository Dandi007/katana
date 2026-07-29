"""Work Folder server configuration、search 与 public envelope 测试。"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import katana_work_folder_mcp.server as server


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Work Folder Test"],
        cwd=root,
        check=True,
    )
    (root / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )


def test_configure_requires_existing_exact_git_root(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        server.configure(str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="existing Git"):
        server.configure(str(tmp_path))

    _init_repo(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    with pytest.raises(ValueError, match="Git repository root"):
        server.configure(str(child))


def test_configure_binds_single_root_without_initializing(tmp_path):
    _init_repo(tmp_path)

    server.configure(str(tmp_path))

    assert server._repo_root == str(tmp_path.resolve())
    assert server._store is not None
    assert server._fs_tools is not None


def test_do_search_shapes_only_flat_id_locators(monkeypatch):
    captured = {}

    def fake_search(query, *, top_k=10):
        captured.update(query=query, top_k=top_k)
        return SimpleNamespace(
            results=[
                SimpleNamespace(
                    path="wf-abc123/findings/note.md",
                    score=0.85,
                    title="Note",
                    snippet="match",
                ),
                SimpleNamespace(
                    path="2026/07/29/legacy.md",
                    score=0.7,
                    title="Legacy",
                    snippet="ignored",
                ),
                SimpleNamespace(
                    path="INDEX.md",
                    score=0.5,
                    title="Index",
                    snippet="ignored",
                ),
            ]
        )

    monkeypatch.setattr(server.vault_search, "search", fake_search)

    result = server._do_search("工作记录", 5)

    assert captured == {"query": "工作记录", "top_k": 5}
    assert result == [
        {
            "folder_id": "wf-abc123",
            "filename": "findings/note.md",
            "score": 0.85,
            "title": "Note",
            "snippet": "match",
        }
    ]


def test_public_payload_drops_internal_locator_fields_and_extracts_mutation_id(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))
    raw = {
        "folder_id": "wf-abc123",
        "filename": "notes.md",
        "path": f"{tmp_path}/wf-abc123/notes.md",
        "changed_paths": ["wf-abc123/notes.md"],
        "manifest": {"manifest_id": "tx-123", "path": str(tmp_path)},
        "nested": {
            "resource_id": "legacy",
            "virtual_path": "wf-abc123/notes.md",
            "message": f"failed under {tmp_path}",
        },
    }

    result = server._public_payload(raw)

    assert result["folder_id"] == "wf-abc123"
    assert result["filename"] == "notes.md"
    assert result["mutation_id"] == "tx-123"
    rendered = json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert not {
        "path",
        "changed_paths",
        "manifest",
        "resource_id",
        "virtual_path",
    } & result.keys()
    assert result["nested"] == {"message": "failed under <work-folder-root>"}


def test_unconfigured_guards(monkeypatch):
    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server, "_fs_tools", None)
    monkeypatch.setattr(server, "_repo_root", None)

    with pytest.raises(RuntimeError, match="configure"):
        server._require_store()
    with pytest.raises(RuntimeError, match="configure"):
        server._require_fs_tools()
