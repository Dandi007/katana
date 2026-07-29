"""test_integration.py — wiki MCP 工具链集成回归 gate。

驱动真实 @mcp.tool() 薄壳（server.wiki_*）+ 真实 pages.py I/O + 真实 git commit，
仅对 vault-search（网络）打桩。覆盖 wiki 的两条强制不变量端到端：
  - wiki_ingest_apply **原子性**：非法 proposal（缺 provenance/outlink）→ 拒、**零落盘、零 commit**
    （server 强于 skill 的硬证据，wiki 侧对应 work-folder 的 BROKEN→blocked）；
    合法 proposal → 真写页 + 反链入既有页 + log.md 追加 + 真 git commit。
  - wiki_query cold：检索真空 → 真写 gap log 行 + 返回 cold=True（禁裸答冒充）。
无 svc / 无 LLM / 无网络。
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

import katana_wiki_mcp.server as server
from katana_kb_mcp_shared import vault_search as vs
from katana_kernel import CASRejectionError


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wiki_repo(tmp_path):
    """tmp git repo 当 wiki_root（== kb_root，scope=None），含一个既有页供反链。"""
    wiki = tmp_path

    def git(*args):
        subprocess.run(["git", "-C", str(wiki), *args],
                       check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "ci@ci")
    git("config", "user.name", "ci")
    # 既有页（接收反链）+ 初始 commit
    (wiki / "existing.md").write_text(
        "---\nid: w-a1b2c3\n创建日期: 2026-06-20 09:00\n"
        "tags: [legacy]\n类型: 卡片\n---\n既有正文 [[某概念]]\n",
        encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")

    server.configure(str(wiki), str(wiki))
    return wiki


def _head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _valid_page(path: str = "新概念.md") -> dict:
    return {
        "path": path,
        "frontmatter": {
            "创建日期": "2026-06-22 10:00",
            "tags": ["test"],
            "类型": "卡片",
            "sources": ["human:测试"],
            "摘要": "一个用于集成测试的概念页",
        },
        "body": "正文内容，关联 [[existing]]。\n",
        "back_updates": [{"path": "existing.md", "title": Path(path).stem}],
    }


# ---------------------------------------------------------------------------
# wiki_ingest_apply — 原子性 + 不变量强制（头号）
# ---------------------------------------------------------------------------

def test_ingest_apply_rejects_invalid_with_zero_writes(wiki_repo):
    head_before = _head(wiki_repo)
    bad = {
        "path": "坏页.md",
        # 缺 sources（provenance）、body 无 wikilink（孤岛）、缺 摘要 → 必拒
        "frontmatter": {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片"},
        "body": "没有任何外链的孤岛正文。\n",
        "back_updates": [],
    }
    res = _run(server.wiki_ingest_apply({"new_pages": [bad], "log_line": "## ingest"}))

    assert res["applied"] is False
    assert "坏页.md" in res["rejected"]
    assert len(res["rejected"]["坏页.md"]) >= 1
    # 零落盘
    assert not (wiki_repo / "坏页.md").exists()
    # 零 commit（HEAD 不变）
    assert _head(wiki_repo) == head_before


@pytest.mark.parametrize("character", ["\x00", "\n", "\t", "\x7f"])
def test_ingest_apply_rejects_control_character_path_with_zero_writes(
    wiki_repo, character
):
    head_before = _head(wiki_repo)
    page = _valid_page()
    path = f"坏{character}页.md"
    page["path"] = path

    res = _run(
        server.wiki_ingest_apply(
            {"new_pages": [page], "log_line": "## control-character path"}
        )
    )

    assert res["applied"] is False
    assert any(
        "control characters" in error
        for error in res["rejected"][path]
    )
    assert _head(wiki_repo) == head_before
    assert not subprocess.run(
        ["git", "-C", str(wiki_repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_ingest_apply_atomic_all_or_nothing(wiki_repo):
    """一好一坏混在一个 proposal：整体拒，好页也不落盘。"""
    head_before = _head(wiki_repo)
    good = _valid_page("好页.md")
    bad = {
        "path": "坏页.md",
        "frontmatter": {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片"},
        "body": "孤岛无外链。\n",
        "back_updates": [],
    }
    res = _run(server.wiki_ingest_apply({"new_pages": [good, bad], "log_line": "## ingest"}))

    assert res["applied"] is False
    assert "坏页.md" in res["rejected"]
    assert not (wiki_repo / "好页.md").exists()   # 原子：好页也不写
    assert not (wiki_repo / "坏页.md").exists()
    assert _head(wiki_repo) == head_before


def test_ingest_apply_success_writes_backlinks_logs_commits(wiki_repo):
    head_before = _head(wiki_repo)
    res = _run(server.wiki_ingest_apply(
        {"new_pages": [_valid_page("新概念.md")],
         "log_line": "## [2026-06-22 10:00] ingest | test"}))

    assert res["applied"] is True
    assert "新概念.md" in res["written"]
    # 真落盘
    assert (wiki_repo / "新概念.md").exists()
    # 反链真写入既有页
    assert "[[新概念]]" in (wiki_repo / "existing.md").read_text(encoding="utf-8")
    assert "existing.md" in res["backlinked"]
    # log.md 真追加
    assert "ingest | test" in (wiki_repo / "log.md").read_text(encoding="utf-8")
    # 真 git commit（HEAD 推进，sha 返回）
    assert res["commit"]
    assert _head(wiki_repo) != head_before


def test_ingest_apply_update_preserves_id_path_backlinks_logs_and_commits(wiki_repo):
    """existing page 必须显式走 updates，并在同一治理事务保留 ID/path。"""
    (wiki_repo / "target.md").write_text(
        "---\n创建日期: 2026-06-20 09:00\ntags: [legacy]\n"
        "类型: 卡片\n---\n目标正文 [[某概念]]\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(wiki_repo), "add", "target.md"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(wiki_repo), "commit", "-m", "seed target"],
        check=True, capture_output=True, text=True,
    )
    head_before = _head(wiki_repo)
    update = {
        "path": "existing.md",
        "frontmatter": {
            "id": "w-a1b2c3",
            "创建日期": "2026-06-22 10:00",
            "tags": ["test"],
            "类型": "卡片",
            "sources": ["conversation 2026-07-29"],
            "摘要": "已更新的既有概念",
        },
        "body": "更新正文，关联 [[target]]。\n",
        "back_updates": [{"path": "target.md", "title": "existing"}],
    }

    res = _run(server.wiki_ingest_apply(
        {"updates": [update],
         "log_line": "## [2026-07-29 13:20] ingest | update-test"},
        expected_base_sha=head_before,
    ))

    assert res["applied"] is True
    assert res["created"] == []
    assert res["updated"] == ["existing.md"]
    existing = (wiki_repo / "existing.md").read_text(encoding="utf-8")
    assert "id: w-a1b2c3" in existing
    assert "更新正文" in existing
    assert "[[existing]]" in (wiki_repo / "target.md").read_text(encoding="utf-8")
    assert "update-test" in (wiki_repo / "log.md").read_text(encoding="utf-8")
    assert _head(wiki_repo) != head_before


def test_ingest_plan_base_sha_rejects_concurrent_change(wiki_repo, monkeypatch):
    def empty_search(query, **kwargs):
        return vs.SearchResponse(results=[], mode="hybrid")

    monkeypatch.setattr(server.vault_search, "search", empty_search)
    plan = _run(server.wiki_ingest_plan("更新 existing"))
    assert plan["base_sha"] == _head(wiki_repo)
    existing_before = (wiki_repo / "existing.md").read_text(encoding="utf-8")

    (wiki_repo / "concurrent.md").write_text("concurrent\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(wiki_repo), "add", "concurrent.md"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(wiki_repo), "commit", "-m", "concurrent"],
        check=True, capture_output=True, text=True,
    )
    update = {
        "path": "existing.md",
        "frontmatter": {
            "id": "w-a1b2c3",
            "创建日期": "2026-06-22 10:00",
            "tags": ["test"],
            "类型": "卡片",
            "sources": ["conversation 2026-07-29"],
            "摘要": "并发保护测试",
        },
        "body": "更新正文 [[concurrent]]。\n",
        "back_updates": [],
    }

    with pytest.raises(CASRejectionError):
        _run(server.wiki_ingest_apply(
            {"updates": [update], "log_line": "## concurrent update"},
            expected_base_sha=plan["base_sha"],
        ))

    assert (wiki_repo / "existing.md").read_text(encoding="utf-8") == existing_before


# ---------------------------------------------------------------------------
# wiki_query — hot / cold（cold 真写 gap log）
# ---------------------------------------------------------------------------

def test_query_hot_returns_candidates_and_contract(wiki_repo, monkeypatch):
    def fake_search(query, **kwargs):
        return vs.SearchResponse(
            results=[vs.SearchResult(path="existing.md", score=0.9, title="既有", snippet="...")],
            mode="hybrid")

    monkeypatch.setattr(server.vault_search, "search", fake_search)
    res = _run(server.wiki_query("某概念是什么"))
    assert res["cold"] is False
    assert res["candidate_count"] == 1
    assert res["candidates"][0]["path"] == "existing.md"
    assert res["synthesis_contract"]


def test_query_cold_writes_gap_log(wiki_repo, monkeypatch):
    def empty_search(query, **kwargs):
        return vs.SearchResponse(results=[], mode="hybrid")

    monkeypatch.setattr(server.vault_search, "search", empty_search)
    res = _run(server.wiki_query("仓库里完全不存在的冷门话题"))
    assert res["cold"] is True
    assert res["candidates"] == []
    # server 真写了 gap log 行到 <wiki_root>/log.md
    log = (wiki_repo / "log.md").read_text(encoding="utf-8")
    assert "gap:" in log
    assert "仓库里完全不存在的冷门话题" in log


# ---------------------------------------------------------------------------
# wiki_search — 路由 + scope
# ---------------------------------------------------------------------------

def test_search_routes_through_vault_search(wiki_repo, monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured["dir"] = kwargs.get("dir")
        return vs.SearchResponse(
            results=[vs.SearchResult(path="a.md", score=0.5, title="A", snippet="s")],
            mode="hybrid")

    monkeypatch.setattr(server.vault_search, "search", fake_search)
    res = _run(server.wiki_search("查询词", top_k=3))
    assert isinstance(res, list)
    assert res[0]["path"] == "a.md"
    assert captured["dir"] == server._scope  # wiki_root==kb_root → None


# --- 追加：wiki_list_docs / wiki_lint_mechanical ---

def test_list_docs_returns_paths_excluding_raw(wiki_repo):
    (wiki_repo / "Zettelkasten").mkdir()
    (wiki_repo / "Zettelkasten" / "甲.md").write_text(
        "---\n类型: 卡片\n摘要: s\n---\n链 [[existing]]\n", encoding="utf-8")
    (wiki_repo / "DeepThought").mkdir()
    (wiki_repo / "DeepThought" / "r.md").write_text(
        "---\n类型: 卡片\n---\nraw\n", encoding="utf-8")
    docs = _run(server.wiki_list_docs())
    paths = [d["path"] for d in docs]
    assert "Zettelkasten/甲.md" in paths
    assert all("DeepThought" not in p for p in paths)


def test_lint_mechanical_reports_broken_link(wiki_repo):
    (wiki_repo / "Zettelkasten").mkdir()
    (wiki_repo / "Zettelkasten" / "甲.md").write_text(
        "---\n创建日期: 2026-06-22 10:00\ntags: [t]\n类型: 卡片\nsources: [human:x]\n摘要: s\n"
        "---\n链 [[不存在]]\n", encoding="utf-8")
    res = _run(server.wiki_lint_mechanical())
    assert any(f["code"] == "broken_link" for f in res["findings"])
