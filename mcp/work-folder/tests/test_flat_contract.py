"""Work Folder P2 flat-storage and ID-only public contract tests."""

from __future__ import annotations

import asyncio
import datetime
import inspect
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from katana_work_folder_mcp import artifacts, brief_ops, reindex, server
from katana_work_folder_mcp.brief import parse_brief
from katana_work_folder_mcp.fs_tools import FSTools, ID_RE


_FORBIDDEN_PUBLIC_KEYS = {
    "folder",
    "index_path",
    "path",
    "resource_id",
    "virtual_path",
}


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Work Folder Test"],
        cwd=repo,
        check=True,
    )
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    (repo / ".gitignore").write_text("/.katana/runtime/\n", encoding="utf-8")
    (repo / "INDEX.md").write_text(reindex.render_index([]), encoding="utf-8")
    controls = repo / ".katana"
    controls.mkdir()
    (controls / "tombstones.json").write_text(
        '{"tombstones": []}\n',
        encoding="utf-8",
    )
    (controls / "flat-layout.json").write_text(
        '{"layout": "flat-id-v1", "schema_version": 1}\n',
        encoding="utf-8",
    )
    (controls / "legacy-manifest-inventory.json").write_text(
        '{"manifests":[],"schema_version":1}\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _assert_public_payload(payload, repo: Path) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, default=str)
    assert str(repo) not in rendered

    def walk(value) -> None:
        if isinstance(value, dict):
            assert _FORBIDDEN_PUBLIC_KEYS.isdisjoint(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)


def _configured_repo(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    server.configure(str(tmp_path))
    monkeypatch.setattr(
        server,
        "_now",
        lambda: datetime.datetime(2026, 7, 29, 16, 0, 0),
    )
    return tmp_path


def _create_folder(monkeypatch, repo: Path, topic: str = "flat contract") -> dict:
    result = asyncio.run(server.wf_create(topic))
    folder_id = result["folder_id"]
    assert ID_RE.fullmatch(folder_id)
    assert (repo / folder_id).is_dir()
    return result


def test_removed_legacy_surface_and_config_contract():
    assert not hasattr(server, "compute_scope")
    assert not hasattr(server, "_ensure_git_repo")
    assert not hasattr(FSTools, "fs_glob")
    assert not hasattr(brief_ops, "derive_id")
    assert not hasattr(brief_ops, "derive_created")
    assert '"KATANA_WORK_FOLDER"' not in inspect.getsource(server)

    assert list(inspect.signature(server.configure).parameters) == ["repo_root"]
    assert list(inspect.signature(server.wf_save).parameters)[0] == "folder_id"
    assert list(inspect.signature(server.wf_resume).parameters)[0] == "folder_id"


def test_full_vfs_signatures_are_id_only():
    assert list(inspect.signature(FSTools.fs_resolve).parameters) == [
        "self",
        "folder_id",
        "filename",
    ]
    assert list(inspect.signature(FSTools.fs_stat).parameters) == [
        "self",
        "folder_id",
        "filename",
    ]
    assert list(inspect.signature(FSTools.fs_list).parameters) == [
        "self",
        "folder_id",
        "dirname",
    ]
    assert list(inspect.signature(FSTools.fs_read).parameters)[:3] == [
        "self",
        "folder_id",
        "filename",
    ]
    assert list(inspect.signature(FSTools.fs_copy).parameters)[:5] == [
        "self",
        "source_folder_id",
        "source_filename",
        "dest_folder_id",
        "dest_filename",
    ]
    assert list(inspect.signature(FSTools.fs_rename).parameters)[:5] == [
        "self",
        "source_folder_id",
        "source_filename",
        "dest_folder_id",
        "dest_filename",
    ]


def test_create_uses_folder_id_as_single_root_directory(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path, monkeypatch)
    result = _create_folder(monkeypatch, repo)
    folder_id = result["folder_id"]

    assert result["created"] is True
    assert result["seeded"] == ["progress.md", "context.md", "_brief.md"]
    assert parse_brief(
        (repo / folder_id / "_brief.md").read_text(encoding="utf-8")
    )["frontmatter"]["id"] == folder_id
    assert list(repo.rglob("progress.md")) == [repo / folder_id / "progress.md"]
    _assert_public_payload(result, repo)


def test_lifecycle_is_id_only_and_never_leaks_repo_path(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path, monkeypatch)
    created = _create_folder(monkeypatch, repo, "lifecycle")
    folder_id = created["folder_id"]

    saved = asyncio.run(server.wf_save(folder_id, summary="checkpoint"))
    resumed = asyncio.run(server.wf_resume(folder_id))

    assert saved["folder_id"] == folder_id
    assert resumed["folder_id"] == folder_id
    assert f"**Work folder ID:** {folder_id}" in (
        repo / folder_id / "CLAUDE.md"
    ).read_text(encoding="utf-8")
    _assert_public_payload(saved, repo)
    _assert_public_payload(resumed, repo)


def test_search_returns_id_and_filename_without_paths(monkeypatch):
    monkeypatch.setattr(
        server.vault_search,
        "search",
        lambda query, top_k: SimpleNamespace(
            results=[
                SimpleNamespace(
                    path="wf-abc123/findings/note.md",
                    score=0.9,
                    title="note",
                    snippet="match",
                )
            ]
        ),
    )

    assert server._do_search("query", 3) == [
        {
            "folder_id": "wf-abc123",
            "filename": "findings/note.md",
            "score": 0.9,
            "title": "note",
            "snippet": "match",
        }
    ]


def test_vfs_read_and_list_envelopes_use_id_and_filename(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path, monkeypatch)
    folder_id = _create_folder(monkeypatch, repo, "vfs")["folder_id"]
    tools = server._fs_tools

    resolved = tools.fs_resolve(folder_id, "_brief.md")
    stat = tools.fs_stat(folder_id, "_brief.md")
    listed = tools.fs_list(folder_id, "")
    read = tools.fs_read(folder_id, "_brief.md")

    for result in (resolved, stat, listed, read):
        assert result["folder_id"] == folder_id
        _assert_public_payload(result, repo)
    assert resolved["filename"] == "_brief.md"
    assert stat["filename"] == "_brief.md"
    assert {entry["filename"] for entry in listed["entries"]} == {
        "_brief.md",
        "context.md",
        "progress.md",
    }
    assert read["filename"] == "_brief.md"


def test_cross_folder_copy_uses_separate_source_and_dest_ids(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path, monkeypatch)
    source_id = _create_folder(monkeypatch, repo, "copy source")["folder_id"]
    dest_id = _create_folder(monkeypatch, repo, "copy dest")["folder_id"]
    tools = server._fs_tools

    tools.fs_create(source_id, "notes.md", "# Notes\n\nsource")
    result = tools.fs_copy(
        source_id,
        "notes.md",
        dest_id,
        "copied.md",
    )

    assert result["folder_id"] == dest_id
    assert result["filename"] == "copied.md"
    assert (repo / dest_id / "copied.md").read_text(encoding="utf-8").endswith(
        "source"
    )
    _assert_public_payload(result, repo)


def test_capabilities_and_registered_tools_delete_fs_glob(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path, monkeypatch)
    capabilities = server._fs_tools.fs_capabilities()
    registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}

    assert "fs_glob" not in capabilities["capabilities"]["operations"]
    assert "fs_glob" not in registered
    _assert_public_payload(capabilities, repo)


def test_resume_guide_and_index_render_only_semantic_ids():
    guide = artifacts.render_resume_guide(
        goal="goal",
        phase="implementation",
        status="active",
        folder_id="wf-abc123",
        key_context="context",
        now="2026-07-29 16:00",
    )
    assert "**Work folder ID:** wf-abc123" in guide
    assert "/data/" not in guide

    rendered_index = reindex.render_index(
        [
            {
                "folder_id": "wf-abc123",
                "fm": {
                    "id": "wf-abc123",
                    "updated": "2026-07-29",
                    "status": "active",
                    "title": "title",
                },
                "goal": "goal",
            }
        ]
    )
    assert "| updated | status | id | title | goal |" in rendered_index
    assert "| folder |" not in rendered_index.lower()
    assert "`wf-abc123`" not in rendered_index
