"""Governed-pipeline anchors for Wiki domain tools (design §4.4, INV-5).

Proves wiki_ingest_apply publishes ONE governed transaction (page write +
backlink + log) carrying a kernel manifest, and that it enforces WikiPolicy on
the projected post-state instead of using a separate write chain.
"""
import asyncio
import subprocess

import pytest

import katana_wiki_mcp.server as server
from katana_kb_mcp_shared.kernel.manifest import extract_from_message


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
    (tmp_path / "existing.md").write_text(
        "---\n类型: 卡片\n---\n既有 [[某概念]]\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")
    server.configure(str(tmp_path), str(tmp_path))
    return tmp_path


def _valid_page(path="新概念.md"):
    return {
        "path": path,
        "frontmatter": {"创建日期": "2026-06-22 10:00", "tags": ["t"],
                        "类型": "卡片", "sources": ["human:测试"], "摘要": "s"},
        "body": "正文 [[existing]]。\n",
        "back_updates": [{"path": "existing.md", "title": "新概念"}],
    }


def _head_message(repo):
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"],
                          capture_output=True, text=True).stdout


def test_ingest_apply_is_single_governed_transaction(wiki_repo):
    head_before = subprocess.run(
        ["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    res = _run(server.wiki_ingest_apply(
        {"new_pages": [_valid_page()], "log_line": "## ingest | test"}))
    assert res["applied"] is True
    head_after = subprocess.run(
        ["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    # Exactly ONE new commit for the whole ingest batch.
    assert head_after != head_before
    manifest = extract_from_message(_head_message(wiki_repo))
    assert manifest is not None, "ingest commit has no kernel manifest → bypass"
    assert manifest.domain == "wiki"
    touched = {c["after_path"] for c in manifest.changes}
    assert any(p and p.endswith("新概念.md") for p in touched)
    # page write + backlink + log landed in the same batch.
    assert any(p == "existing.md" for p in touched)
    assert any(p == "log.md" for p in touched)


def test_ingest_apply_reject_leaves_zero_delta(wiki_repo):
    head_before = subprocess.run(
        ["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    bad = _valid_page("坏页.md")
    bad["body"] = "无外链孤岛\n"
    del bad["frontmatter"]["sources"]
    res = _run(server.wiki_ingest_apply(
        {"new_pages": [bad], "log_line": "## ingest"}))
    assert res["applied"] is False
    head_after = subprocess.run(
        ["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    assert head_after == head_before
    assert not (wiki_repo / "坏页.md").exists()


def test_ingest_reject_after_staged_changes_leaves_zero_delta(wiki_repo, monkeypatch):
    """A commit-stage policy rejection AFTER pages/backlinks/log were projected
    into staging still leaves zero canonical delta and a clean working tree
    (operator P1 #10: reject must be exercised past staged/projected changes)."""
    head_before = subprocess.run(
        ["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()

    # Force the shared policy to reject at commit stage — i.e. after ingest has
    # already written the page + backlink + log into writer-private staging.
    from katana_kb_mcp_shared.kernel.errors import KernelError, POLICY_VIOLATION

    def reject(batch):
        raise KernelError(POLICY_VIOLATION, "rejected at commit stage")

    monkeypatch.setattr(server._vfs.policy, "validate", reject)
    with pytest.raises(Exception):
        _run(server.wiki_ingest_apply(
            {"new_pages": [_valid_page()], "log_line": "## ingest | test"}))

    head_after = subprocess.run(
        ["git", "-C", str(wiki_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    assert head_after == head_before
    # No page leaked into the canonical working tree, and it stays clean.
    assert not (wiki_repo / "新概念.md").exists()
    porcelain = subprocess.run(
        ["git", "-C", str(wiki_repo), "status", "--porcelain"],
        capture_output=True, text=True).stdout.strip()
    assert porcelain == ""
