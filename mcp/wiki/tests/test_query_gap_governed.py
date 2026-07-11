"""wiki_query cold-path gap logging is non-canonical (operator P0 #3).

A cold query must NOT raw-append a canonical file: it records the coverage gap
as an operational event under the git-excluded reserved namespace, so the
working tree stays clean and no ungoverned canonical mutation occurs.
"""
import asyncio
import subprocess

import pytest

import katana_wiki_mcp.server as server
from katana_kb_mcp_shared import vault_search as vs


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wiki_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "ci@ci")
    git("config", "user.name", "ci")
    (tmp_path / "log.md").write_text("# Log\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")
    server.configure(str(tmp_path), str(tmp_path))
    return tmp_path


def _porcelain(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip()


def test_cold_query_leaves_working_tree_clean(wiki_repo, monkeypatch):
    monkeypatch.setattr(server.vault_search, "search",
                        lambda q, **k: vs.SearchResponse(results=[], mode="hybrid"))
    before = subprocess.run(["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    out = _run(server.wiki_query("完全不存在的冷主题"))
    assert out["cold"] is True
    # No canonical mutation: HEAD unchanged and working tree clean.
    after = subprocess.run(["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    assert after == before
    assert _porcelain(wiki_repo) == ""
    # canonical log.md was NOT raw-appended.
    assert (wiki_repo / "log.md").read_text(encoding="utf-8") == "# Log\n"
    # The gap was recorded in the operational (git-excluded) sink.
    gap = wiki_repo / ".kb" / "query-gaps.log"
    assert gap.exists()
    assert "完全不存在的冷主题" in gap.read_text(encoding="utf-8")
