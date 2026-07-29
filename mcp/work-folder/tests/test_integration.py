"""真实 server shell + kernel + Git 的 flat Work Folder lifecycle 集成门。"""

from __future__ import annotations

import asyncio
import datetime
import json
import subprocess
from pathlib import Path

import pytest

import katana_work_folder_mcp.server as server
from katana_work_folder_mcp import lifecycle, reindex


def _run(coro):
    return asyncio.run(coro)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
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
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


def _context_snapshot(resource_path: str, branch: str = "-") -> str:
    return (
        "# Context\n\n**Updated:** 2026-07-29 16:00\n\n"
        "## 工作上下文\n- 集成测试\n\n"
        "## 关键路径\n"
        "| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |\n"
        "|------|------------|------------|------|\n"
        f"| target | {resource_path} | {branch} | 探测目标 |\n\n"
        "## 环境信息\n- test\n"
    )


def _assert_public(payload, repo: Path) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, default=str)
    assert str(repo) not in rendered
    forbidden = {
        "path",
        "folder",
        "wf_abs",
        "absolute_path",
        "index_path",
        "resource_id",
        "virtual_path",
    }

    def walk(value):
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    server.configure(str(tmp_path))
    monkeypatch.setattr(
        server,
        "_now",
        lambda: datetime.datetime(2026, 7, 29, 16, 0, 0),
    )
    return tmp_path


def test_lifecycle_create_save_resume_match(configured, tmp_path):
    created = _run(server.wf_create("集成测试 冒烟"))
    folder_id = created["folder_id"]
    folder = configured / folder_id
    assert folder.parent == configured
    assert (folder / "progress.md").is_file()
    assert (folder / "context.md").is_file()

    saved = _run(server.wf_save(folder_id, summary="集成存档"))
    assert saved["saved"] is True
    assert (folder / "CLAUDE.md").read_text(encoding="utf-8") == (
        folder / "AGENTS.md"
    ).read_text(encoding="utf-8")
    assert "集成存档" in (folder / "progress.md").read_text(encoding="utf-8")

    plain = tmp_path / "exists-plain"
    plain.mkdir()
    _run(
        server.wf_save(
            folder_id,
            summary="更新关键路径",
            context_snapshot=_context_snapshot(str(plain)),
        )
    )
    resumed = _run(server.wf_resume(folder_id))
    assert resumed["verification"]["overall"] == "MATCH"
    assert resumed["blocked"] is False
    assert "resume" in (folder / "progress.md").read_text(encoding="utf-8")
    for result in (created, saved, resumed):
        _assert_public(result, configured)


def test_resume_broken_blocks(configured):
    folder_id = _run(server.wf_create("broken 场景"))["folder_id"]
    missing = "/nonexistent/wf-mcp-integration-xyz"
    _run(
        server.wf_save(
            folder_id,
            summary="更新关键路径",
            context_snapshot=_context_snapshot(missing),
        )
    )

    result = _run(server.wf_resume(folder_id))

    assert result["ok"] is True
    assert result["verification"]["overall"] == "BROKEN"
    assert result["blocked"] is True
    assert result["contract"] == lifecycle.RESUME_BLOCKED_CONTRACT
    assert missing in result["resume_report"]


def test_resume_drift_real_dirty_git(configured, tmp_path_factory):
    repo = tmp_path_factory.mktemp("probe-repo")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "untracked.txt").write_text("dirty", encoding="utf-8")

    folder_id = _run(server.wf_create("drift 场景"))["folder_id"]
    _run(
        server.wf_save(
            folder_id,
            context_snapshot=_context_snapshot(str(repo)),
        )
    )

    result = _run(server.wf_resume(folder_id))

    assert result["verification"]["overall"] == "DRIFT"
    assert result["blocked"] is False


def test_missing_and_noncanonical_folder_ids_fail_safe(configured):
    missing = _run(server.wf_resume("wf-deadbe"))
    invalid = _run(server.wf_resume("../escape"))

    assert missing["ok"] is False and missing["blocked"] is True
    assert invalid["ok"] is False and invalid["blocked"] is True
    _assert_public(missing, configured)
    _assert_public(invalid, configured)


def test_list_search_and_reindex_are_locator_free(configured, monkeypatch):
    first = _run(server.wf_create("候选一"))["folder_id"]
    second = _run(server.wf_create("候选二"))["folder_id"]

    listed = _run(server.wf_list(limit=5))
    assert {item["folder_id"] for item in listed["candidates"]} >= {first, second}

    monkeypatch.setattr(
        server.vault_search,
        "search",
        lambda query, top_k: type(
            "Response",
            (),
            {
                "results": [
                    type(
                        "Hit",
                        (),
                        {
                            "path": f"{first}/findings.md",
                            "score": 0.9,
                            "title": "X",
                            "snippet": "...",
                        },
                    )()
                ]
            },
        )(),
    )
    searched = _run(server.wf_search("候选", top_k=3))
    assert searched == [
        {
            "folder_id": first,
            "filename": "findings.md",
            "score": 0.9,
            "title": "X",
            "snippet": "...",
        }
    ]

    indexed = _run(server.wf_reindex())
    assert indexed["indexed"] >= 2
    assert (configured / "INDEX.md").is_file()
    for result in (listed, searched, indexed):
        _assert_public(result, configured)
