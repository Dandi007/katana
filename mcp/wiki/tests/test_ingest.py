import subprocess
from pathlib import Path
import pytest
from katana_wiki_mcp import ingest, invariants, pages
from katana_kb_mcp_shared import vault_search as vs


def _git(root, *a): subprocess.run(["git","-C",str(root),*a],check=True,capture_output=True,text=True)


@pytest.fixture
def wiki(tmp_path):
    _git(tmp_path,"init","-q"); _git(tmp_path,"config","user.email","t@t"); _git(tmp_path,"config","user.name","t")
    (tmp_path/"log.md").write_text("# Log\n",encoding="utf-8")
    # 一个被反链的存量页
    (tmp_path/"页B.md").write_text("---\n类型: 卡片\n---\nB 正文\n",encoding="utf-8")
    _git(tmp_path,"add","-A"); _git(tmp_path,"commit","-qm","init")
    return tmp_path


def _valid_new_page(path="新概念.md"):
    return {"path": path,
            "frontmatter": {"创建日期":"2026-06-22 10:00","tags":["x"],"类型":"卡片","sources":["raw/a.md"],"摘要":"新概念一句话"},
            "body": "正文 [[页B]]\n",
            "back_updates": [{"path":"页B.md","title":"新概念"}]}


def test_plan_returns_candidates_and_instructions():
    def fake_search(q,*,top_k=10,dir=None,exclude=None,base_url="",client=None):
        return vs.SearchResponse(results=[vs.SearchResult(path="页B.md",score=0.5,title="B",snippet="s")],mode="hybrid")
    out = ingest.plan("一些待入库内容", "Zettelkasten", search_fn=fake_search)
    assert out["candidates"][0]["path"] == "页B.md"
    assert "create_vs_update" in out["instructions"]
    assert "proposal_schema" in out


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
