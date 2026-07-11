"""Governed Full VFS (fs_*) parity for the Wiki app (design §5.2, INV-5)."""
import asyncio
import subprocess

import pytest
from fastmcp import Client

import katana_wiki_mcp.server as server


@pytest.fixture
def wiki_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "ci@ci")
    git("config", "user.name", "ci")
    (tmp_path / "existing.md").write_text(
        "---\n类型: 卡片\n---\n正文 [[某概念]]\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")
    server.configure(str(tmp_path), str(tmp_path))
    return tmp_path


def _call(tool, args=None):
    async def go():
        async with Client(server.mcp) as c:
            return (await c.call_tool(tool, args or {})).data
    return asyncio.run(go())


def _tool_names():
    async def go():
        async with Client(server.mcp) as c:
            return {t.name for t in await c.list_tools()}
    return asyncio.run(go())


def test_six_domain_tools_plus_fs_facade(wiki_repo):
    names = _tool_names()
    domain = {"wiki_search", "wiki_query", "wiki_ingest_plan",
              "wiki_ingest_apply", "wiki_list_docs", "wiki_lint_mechanical"}
    fs = {"fs_read", "fs_list", "fs_stat", "fs_create", "fs_edit"}
    assert domain <= names, f"missing: {domain - names}"
    assert fs <= names, f"missing: {fs - names}"


def test_fs_create_governed_page_and_read(wiki_repo):
    page = ("---\n创建日期: 2026-07-11\ntags:\n  - x\n类型: 卡片\n摘要: s\n"
            "---\n正文 [[某概念]]\n\n# References\n- src\n")
    r = _call("fs_create", {"virtual_path": "Zettelkasten/new.md", "content": page})
    assert r["commit_sha"]
    assert r["resource_id"].startswith("w-")
    rd = _call("fs_read", {"virtual_path": "Zettelkasten/new.md"})
    assert "正文" in rd["content"]


def test_fs_create_bad_page_rejected(wiki_repo):
    bad = "---\n类型: 卡片\n---\n无 outlink\n"
    with pytest.raises(Exception):
        _call("fs_create", {"virtual_path": "Zettelkasten/bad.md", "content": bad})


def test_fs_create_raw_zone_exempt(wiki_repo):
    r = _call("fs_create", {"virtual_path": "raw/report.md",
                            "content": "plain raw text\n"})
    assert r["commit_sha"]


def test_fs_traversal_rejected(wiki_repo):
    with pytest.raises(Exception):
        _call("fs_read", {"virtual_path": "../../etc/passwd"})
