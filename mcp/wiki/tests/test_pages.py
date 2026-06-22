import subprocess
from pathlib import Path
import pytest
from katana_wiki_mcp import pages


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def wiki(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "log.md").write_text("# Log\n", encoding="utf-8")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_parse_roundtrip():
    fm = {"创建日期": "2026-06-22 10:00", "tags": ["a"], "类型": "卡片"}
    text = pages.render_page(fm, "正文 [[x]]\n")
    fm2, body2 = pages.parse_page(text)
    assert fm2 == fm
    assert body2.strip() == "正文 [[x]]"


def test_parse_no_frontmatter():
    fm, body = pages.parse_page("纯正文无 frontmatter")
    assert fm == {} and "纯正文" in body


def test_write_and_read_page(wiki):
    p = wiki / "Zettelkasten" / "页A.md"
    pages.write_page(str(p), {"类型": "卡片", "tags": ["a"]}, "正文 [[页B]]\n")
    fm, body = pages.read_page(str(p))
    assert fm["类型"] == "卡片" and "[[页B]]" in body


def test_ensure_backlink_adds_when_missing(wiki):
    p = wiki / "页B.md"
    pages.write_page(str(p), {"类型": "卡片"}, "B 的正文，没链接\n")
    added = pages.ensure_backlink(str(p), "页A")
    assert added is True
    _, body = pages.read_page(str(p))
    assert "[[页A]]" in body
    # 幂等：再调不重复加
    assert pages.ensure_backlink(str(p), "页A") is False


def test_append_log_creates_and_appends(wiki):
    pages.append_log(str(wiki), "## [2026-06-22 10:00] ingest | 测试")
    txt = (wiki / "log.md").read_text(encoding="utf-8")
    assert "ingest | 测试" in txt and txt.endswith("\n")


def test_git_commit_returns_sha(wiki):
    (wiki / "新页.md").write_text("x", encoding="utf-8")
    sha = pages.git_commit(str(wiki), "wiki: add 新页", ["新页.md"])
    assert len(sha) >= 7
    log = subprocess.run(["git", "-C", str(wiki), "log", "--oneline", "-1"],
                         capture_output=True, text=True).stdout
    assert "add 新页" in log


def test_archive_inbox_git_mv(wiki):
    inbox = wiki / "inbox"; inbox.mkdir()
    f = inbox / "src.md"; f.write_text("源", encoding="utf-8")
    _git(wiki, "add", "-A"); _git(wiki, "commit", "-qm", "inbox src")
    newrel = pages.archive_inbox(str(f), str(wiki / "raw"), str(wiki))
    assert "raw" in newrel
    assert (wiki / newrel).exists() and not f.exists()
