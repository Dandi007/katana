import subprocess
from pathlib import Path
import pytest
from katana_wiki_mcp import ingest, invariants, pages
from katana_kb_mcp_shared import vault_search as vs


def _git(root, *a): subprocess.run(["git","-C",str(root),*a],check=True,capture_output=True,text=True)
def _head(root): return subprocess.run(["git","-C",str(root),"rev-parse","HEAD"],check=True,capture_output=True,text=True).stdout.strip()


@pytest.fixture
def wiki(tmp_path):
    _git(tmp_path,"init","-q"); _git(tmp_path,"config","user.email","t@t"); _git(tmp_path,"config","user.name","t")
    (tmp_path/"log.md").write_text("# Log\n",encoding="utf-8")
    # 一个被反链的存量页
    (tmp_path/"页B.md").write_text(
        "---\n创建日期: 2026-06-20 09:00\ntags: [legacy]\n类型: 卡片\n"
        "---\nB 正文\n",
        encoding="utf-8",
    )
    _git(tmp_path,"add","-A"); _git(tmp_path,"commit","-qm","init")
    return tmp_path


def _valid_new_page(path="新概念.md"):
    return {"path": path,
            "frontmatter": {"创建日期":"2026-06-22 10:00","tags":["x"],"类型":"卡片","sources":["raw/a.md"],"摘要":"新概念一句话"},
            "body": "正文 [[页B]]\n",
            "back_updates": [{"path":"页B.md","title":"新概念"}]}


def _valid_update(path="既有概念.md", page_id="w-a1b2c3"):
    return {
        "path": path,
        "frontmatter": {
            "id": page_id,
            "创建日期": "2026-06-22 10:00",
            "tags": ["x"],
            "类型": "卡片",
            "sources": ["raw/a.md", "conversation 2026-07-29"],
            "摘要": "更新后的既有概念",
        },
        "body": "更新正文 [[页B]]\n",
        "back_updates": [{"path": "页B.md", "title": "既有概念"}],
    }


def test_plan_returns_candidates_and_instructions():
    def fake_search(q,*,top_k=10,dir=None,exclude=None,base_url="",client=None):
        return vs.SearchResponse(results=[vs.SearchResult(path="页B.md",score=0.5,title="B",snippet="s")],mode="hybrid")
    out = ingest.plan(
        "一些待入库内容",
        "Zettelkasten",
        search_fn=fake_search,
        base_sha="a" * 40,
    )
    assert out["candidates"][0]["path"] == "页B.md"
    assert "create_vs_update" in out["instructions"]
    assert "proposal_schema" in out
    assert "'updates'" in out["proposal_schema"]
    assert "preserve existing id" in out["proposal_schema"]
    assert out["base_sha"] == "a" * 40
    assert "base_commit_sha" not in out


def test_apply_valid_writes_backlinks_logs_commits(wiki):
    prop = {"new_pages":[_valid_new_page()], "log_line":"## [2026-06-22 10:00] ingest | 测试源"}
    out = ingest.apply(prop, str(wiki),
                       validate_fn=invariants.validate_page, write_fn=pages.write_page,
                       backlink_fn=pages.ensure_backlink, log_fn=pages.append_log, commit_fn=pages.git_commit)
    assert out["applied"] is True
    assert (wiki/"新概念.md").exists()
    _, bbody = pages.read_page(str(wiki/"页B.md"))
    assert "[[新概念]]" in bbody                       # 反向链接自动写了
    assert "ingest | 测试源" in (wiki/"log.md").read_text(encoding="utf-8")
    log = subprocess.run(["git","-C",str(wiki),"log","--oneline","-1"],capture_output=True,text=True).stdout
    assert "ingest" in log


# ---- 负向（C 中枢头等公民）：缺 provenance 的提案被拒 + 零落盘 ----
def test_apply_rejects_missing_provenance_zero_writes(wiki):
    bad = _valid_new_page("脏页.md"); del bad["frontmatter"]["sources"]  # 无 sources（卡片非 CODE 类，require_sources 默认 True → 拒）
    bad["body"] = "正文 [[页B]] 无来源\n"  # 也无 # References
    before = subprocess.run(["git","-C",str(wiki),"rev-parse","HEAD"],capture_output=True,text=True).stdout
    out = ingest.apply({"new_pages":[bad],"log_line":"x"}, str(wiki),
                       validate_fn=invariants.validate_page, write_fn=pages.write_page,
                       backlink_fn=pages.ensure_backlink, log_fn=pages.append_log, commit_fn=pages.git_commit)
    assert out["applied"] is False
    assert "脏页.md" in out["rejected"]
    assert any("sources" in e for e in out["rejected"]["脏页.md"])
    assert not (wiki/"脏页.md").exists()              # 零落盘
    after = subprocess.run(["git","-C",str(wiki),"rev-parse","HEAD"],capture_output=True,text=True).stdout
    assert before == after                            # 无 commit


def test_apply_rejects_island_no_outlink(wiki):
    bad = _valid_new_page("孤岛.md"); bad["body"] = "正文完全没有 wikilink\n"
    before = subprocess.run(["git","-C",str(wiki),"rev-parse","HEAD"],capture_output=True,text=True).stdout
    out = ingest.apply({"new_pages":[bad],"log_line":"x"}, str(wiki),
                       validate_fn=invariants.validate_page, write_fn=pages.write_page,
                       backlink_fn=pages.ensure_backlink, log_fn=pages.append_log, commit_fn=pages.git_commit)
    assert out["applied"] is False
    assert any("孤岛" in e for e in out["rejected"]["孤岛.md"])
    assert not (wiki/"孤岛.md").exists()
    after = subprocess.run(["git","-C",str(wiki),"rev-parse","HEAD"],capture_output=True,text=True).stdout
    assert before == after                            # 无 commit


def test_apply_updates_only_really_updates_and_preserves_id(wiki):
    existing = _valid_update()
    pages.write_page(
        str(wiki / existing["path"]),
        {
            **existing["frontmatter"],
            "sources": ["raw/a.md"],
            "摘要": "更新前的既有概念",
        },
        "旧正文 [[页B]]\n",
    )
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed update page")

    out = ingest.apply(
        {"updates": [existing], "log_line": "## ingest | update helper"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
        expected_base_sha=_head(wiki),
    )

    assert out["applied"] is True
    assert out["created"] == []
    assert out["updated"] == [existing["path"]]
    fm, body = pages.read_page(str(wiki / existing["path"]))
    assert fm["id"] == "w-a1b2c3"
    assert body == "更新正文 [[页B]]\n"
    assert "update helper" in (wiki / "log.md").read_text(encoding="utf-8")


def test_apply_update_id_mismatch_rejected_with_zero_writes(wiki):
    existing = _valid_update()
    pages.write_page(
        str(wiki / existing["path"]),
        existing["frontmatter"],
        "旧正文 [[页B]]\n",
    )
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed update page")
    proposal = {"updates": [_valid_update(page_id="w-dead00")], "log_line": "x"}
    before_content = (wiki / existing["path"]).read_text(encoding="utf-8")
    before_head = subprocess.run(
        ["git", "-C", str(wiki), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout

    out = ingest.apply(
        proposal,
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
        expected_base_sha=_head(wiki),
    )

    assert out["applied"] is False
    assert any("mismatch" in e for e in out["rejected"][existing["path"]])
    assert (wiki / existing["path"]).read_text(encoding="utf-8") == before_content
    after_head = subprocess.run(
        ["git", "-C", str(wiki), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert after_head == before_head


def test_apply_canonicalizes_before_duplicate_detection(wiki):
    first = _valid_new_page("A.md")
    first["back_updates"][0]["title"] = "A"
    second = _valid_new_page("./A.md")
    second["back_updates"][0]["title"] = "A"

    out = ingest.apply(
        {"new_pages": [first, second], "log_line": "x"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any("重复" in e for e in out["rejected"]["A.md"])
    assert not (wiki / "A.md").exists()


def test_apply_rejects_parent_traversal_with_zero_writes(wiki):
    page = _valid_new_page("../escape.md")
    page["back_updates"][0]["title"] = "escape"

    out = ingest.apply(
        {"new_pages": [page], "log_line": "x"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any("inside wiki_root" in e for e in out["rejected"]["../escape.md"])
    assert not (wiki.parent / "escape.md").exists()


def test_apply_rejects_symlink_path_with_zero_writes(wiki):
    outside = wiki.parent / "outside"
    outside.mkdir()
    (wiki / "link").symlink_to(outside, target_is_directory=True)
    page = _valid_new_page("link/escape.md")
    page["back_updates"][0]["title"] = "escape"

    out = ingest.apply(
        {"new_pages": [page], "log_line": "x"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any("symlink" in e for e in out["rejected"]["link/escape.md"])
    assert not (outside / "escape.md").exists()


def test_apply_rejects_non_string_log_line_before_write(wiki):
    page = _valid_new_page("结构错误.md")
    page["back_updates"][0]["title"] = "结构错误"

    out = ingest.apply(
        {"new_pages": [page], "log_line": 123},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert "log_line must be a string" in out["rejected"]["(proposal)"]
    assert not (wiki / "结构错误.md").exists()


@pytest.mark.parametrize("target", ["target-dir", ".gitignore"])
def test_apply_rejects_non_page_backlink_target(wiki, target):
    if target == "target-dir":
        (wiki / target).mkdir()
    else:
        (wiki / target).write_text("*.tmp\n", encoding="utf-8")
    page = _valid_new_page("受治理目标.md")
    page["back_updates"] = [{"path": target, "title": "受治理目标"}]

    out = ingest.apply(
        {"new_pages": [page], "log_line": "x"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any(
        "governed wiki page" in error or "regular .md" in error
        for error in out["rejected"]["受治理目标.md"]
    )
    assert not (wiki / "受治理目标.md").exists()


def test_apply_detects_unicode_nfc_nfd_duplicate(wiki):
    nfc = _valid_new_page("é.md")
    nfd = _valid_new_page("e\u0301.md")
    nfc["back_updates"][0]["title"] = "é"
    nfd["back_updates"][0]["title"] = "é"

    out = ingest.apply(
        {"new_pages": [nfc, nfd], "log_line": "x"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any("重复" in error for error in out["rejected"]["é.md"])


@pytest.mark.parametrize(
    "path", ["C:/x.md", r"C:\x.md", r"\\server\share\x.md"]
)
def test_apply_rejects_windows_paths(wiki, path):
    page = _valid_new_page(path)

    out = ingest.apply(
        {"new_pages": [page], "log_line": "x"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any("Windows" in error for error in out["rejected"][path])


@pytest.mark.parametrize("character", ["\x00", "\n", "\t", "\x7f"])
def test_canonical_path_rejects_control_characters_before_path_access(
    wiki, monkeypatch, character
):
    class UnexpectedPathAccess:
        def __init__(self, *_args, **_kwargs):
            pytest.fail("control-character paths must be rejected before Path access")

    monkeypatch.setattr(ingest, "Path", UnexpectedPathAccess)
    path = f"Zettelkasten/坏{character}页.md"

    canonical, error = ingest._canonical_path(str(wiki), path)

    assert canonical is None
    assert error is not None
    assert "control characters" in error


def test_canonical_path_preserves_legal_unicode(wiki):
    path = "Zettelkasten/合法-é-😀.md"

    canonical, error = ingest._canonical_path(str(wiki), path)

    assert canonical == path
    assert error is None


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("---\nid: w-a1b2c3\n正文无 closing delimiter\n", "paired"),
        ("---\n{}\n---\n空 frontmatter\n", "frontmatter invalid"),
    ],
)
def test_apply_rejects_backlink_target_without_page_identity(
    wiki, content, expected
):
    (wiki / "target.md").write_text(content, encoding="utf-8")
    page = _valid_new_page("身份页.md")
    page["back_updates"] = [{"path": "target.md", "title": "身份页"}]

    out = ingest.apply(
        {"new_pages": [page], "log_line": "x"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any(expected in error for error in out["rejected"]["身份页.md"])
    assert not (wiki / "身份页.md").exists()


@pytest.mark.parametrize("path", ["a:b.md", "C:语言.md"])
def test_apply_allows_posix_colon_filenames(wiki, path):
    page = _valid_new_page(path)
    page["back_updates"] = []

    out = ingest.apply(
        {"new_pages": [page], "log_line": "## colon path"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is True
    assert (wiki / path).is_file()


def test_apply_rejects_deepthought_precious_overwrite_zero_dirty(wiki):
    precious = wiki / "DeepThought" / "PRECIOUS.md"
    precious.parent.mkdir()
    original = b"PRECIOUS RAW BYTES\n\x00\x01"
    precious.write_bytes(original)
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed precious raw")
    page = _valid_new_page("DeepThought/PRECIOUS.md")
    page["back_updates"] = []
    head_before = _head(wiki)

    out = ingest.apply(
        {"new_pages": [page], "log_line": "overwrite raw"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any(
        "governed writable" in error
        for error in out["rejected"]["DeepThought/PRECIOUS.md"]
    )
    assert precious.read_bytes() == original
    assert _head(wiki) == head_before
    assert subprocess.run(
        ["git", "-C", str(wiki), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout == ""


def test_apply_raw_duplicate_ids_do_not_block_governed_ingest(wiki):
    raw = wiki / "raw"
    raw.mkdir()
    duplicate = (
        "---\nid: w-a1b2c3\n创建日期: 2026-06-20 09:00\n"
        "tags: [raw]\n类型: 卡片\n---\nraw\n"
    )
    (raw / "a.md").write_text(duplicate, encoding="utf-8")
    (raw / "b.md").write_text(duplicate, encoding="utf-8")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed raw duplicate ids")
    page = _valid_new_page("Zettelkasten/安全页.md")
    page["back_updates"] = []

    out = ingest.apply(
        {"new_pages": [page], "log_line": "safe governed write"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is True
    assert (wiki / "Zettelkasten" / "安全页.md").is_file()


def test_apply_updates_legacy_page_without_adding_id(wiki):
    update = _valid_update("legacy.md")
    update["frontmatter"].pop("id")
    update["back_updates"] = []
    existing_fm = dict(update["frontmatter"])
    existing_fm["sources"] = ["raw/a.md"]
    pages.write_page(str(wiki / "legacy.md"), existing_fm, "旧正文 [[页B]]\n")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed legacy page")

    out = ingest.apply(
        {"updates": [update], "log_line": "legacy update"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
        expected_base_sha=_head(wiki),
    )

    assert out["applied"] is True
    fm, body = pages.read_page(str(wiki / "legacy.md"))
    assert "id" not in fm
    assert body == "更新正文 [[页B]]\n"


def test_apply_rejects_forged_id_on_legacy_page(wiki):
    proposal = _valid_update("legacy.md")
    existing_fm = dict(proposal["frontmatter"])
    existing_fm.pop("id")
    pages.write_page(str(wiki / "legacy.md"), existing_fm, "旧正文 [[页B]]\n")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed legacy page")
    before = (wiki / "legacy.md").read_bytes()

    out = ingest.apply(
        {"updates": [proposal], "log_line": "forge id"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
        expected_base_sha=_head(wiki),
    )

    assert out["applied"] is False
    assert any(
        "must not add or forge id" in error
        for error in out["rejected"]["legacy.md"]
    )
    assert (wiki / "legacy.md").read_bytes() == before


def test_apply_rejects_existing_png_new_page_zero_dirty(wiki):
    asset = wiki / "Zettelkasten" / "asset.png"
    asset.parent.mkdir()
    original = b"\x89PNG\r\nPRECIOUS"
    asset.write_bytes(original)
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed png")
    page = _valid_new_page("Zettelkasten/asset.png")
    page["back_updates"] = []
    head_before = _head(wiki)

    out = ingest.apply(
        {"new_pages": [page], "log_line": "overwrite png"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any(
        "must end with .md" in error
        for error in out["rejected"]["Zettelkasten/asset.png"]
    )
    assert asset.read_bytes() == original
    assert _head(wiki) == head_before
    assert subprocess.run(
        ["git", "-C", str(wiki), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout == ""


def test_apply_rejects_regular_file_path_ancestor_zero_dirty(wiki):
    parent = wiki / "Zettelkasten" / "parent"
    parent.parent.mkdir()
    original = b"PARENT FILE BYTES\n"
    parent.write_bytes(original)
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed parent file")
    page = _valid_new_page("Zettelkasten/parent/child.md")
    page["back_updates"] = []
    head_before = _head(wiki)

    out = ingest.apply(
        {"new_pages": [page], "log_line": "child under file"},
        str(wiki),
        validate_fn=invariants.validate_page,
        write_fn=pages.write_page,
        read_fn=pages.read_page,
        backlink_fn=pages.ensure_backlink,
        log_fn=pages.append_log,
        commit_fn=pages.git_commit,
    )

    assert out["applied"] is False
    assert any(
        "ancestor must be a non-symlink directory" in error
        for error in out["rejected"]["Zettelkasten/parent/child.md"]
    )
    assert parent.read_bytes() == original
    assert _head(wiki) == head_before
    assert subprocess.run(
        ["git", "-C", str(wiki), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout == ""
