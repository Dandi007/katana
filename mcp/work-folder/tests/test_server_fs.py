"""Governed Full VFS (fs_*) parity for the Work Folder app (design §5.2, INV-5)."""
import asyncio
import subprocess

import pytest
from fastmcp import Client

import katana_work_folder_mcp.server as server
from katana_work_folder_mcp import brief as _brief


@pytest.fixture
def wf_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "ci@ci")
    git("config", "user.name", "ci")
    (tmp_path / "seed.md").write_text("seed\n", encoding="utf-8")
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


def test_six_domain_tools_plus_fs_facade(wf_repo):
    names = _tool_names()
    domain = {"wf_search", "wf_create", "wf_list", "wf_save", "wf_resume",
              "wf_reindex"}
    from katana_kb_mcp_shared.kernel.facade import FS_FACADE
    assert domain <= names, f"missing: {domain - names}"
    assert FS_FACADE <= names, f"missing fs_*: {FS_FACADE - names}"


def test_fs_create_brief_and_read(wf_repo):
    brief = _brief.render_brief(
        id="wf-aaaaaa", title="t", status="active",
        created="2026-07-11", updated="2026-07-11", goal="g", summary="s")
    r = _call("fs_create", {"virtual_path": "2026/07/11/x/_brief.md",
                            "content": brief})
    assert r["commit_sha"]
    assert r["resource_id"].startswith("wf-")
    rd = _call("fs_read", {"virtual_path": "2026/07/11/x/_brief.md"})
    assert "Goal" in rd["content"]


def test_fs_create_bad_brief_rejected(wf_repo):
    with pytest.raises(Exception):
        _call("fs_create", {"virtual_path": "2026/07/11/x/_brief.md",
                            "content": "no frontmatter\n"})


def test_fs_create_document_passes(wf_repo):
    r = _call("fs_create", {"virtual_path": "2026/07/11/x/progress.md",
                            "content": "free notes\n"})
    assert r["commit_sha"]


def test_fs_traversal_rejected(wf_repo):
    with pytest.raises(Exception):
        _call("fs_read", {"virtual_path": "../../etc/passwd"})
