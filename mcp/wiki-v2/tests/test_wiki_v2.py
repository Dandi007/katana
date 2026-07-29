"""Comprehensive tests for mcp/wiki-v2/ — covers all acceptance criteria A1-A8.

Test organization mirrors spec §8:
  A1 — write tool three-state (validation reject / success / manifest+commit)
  A2 — rename regression
  A3 — delete regression
  A4 — INV-1 (no mutating fs_* tools), INV-2 (id immutability)
  A5 — search (fake embedder / error embedder)
  A6 — INV-6 (bad page isolation)
  A7 — migration CLI
  A8 — concurrent write serialization
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "katana_wiki_v2_mcp"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from katana_wiki_v2_mcp import invariants as _inv
from katana_wiki_v2_mcp import pages as _pages
from katana_wiki_v2_mcp import query as _query
from katana_wiki_v2_mcp import search as _search
from katana_wiki_v2_mcp import vfs as _vfs
from katana_wiki_v2_mcp.store import WikiStore


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_data_root() -> str:
    d = tempfile.mkdtemp(prefix="wiki_v2_test_")
    subprocess.run(["git", "init", d], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "test@test"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (Path(d) / "pages").mkdir(parents=True, exist_ok=True)
    (Path(d) / ".katana" / "manifests").mkdir(parents=True, exist_ok=True)
    (Path(d) / ".katana" / "index").mkdir(parents=True, exist_ok=True)
    (Path(d) / ".gitignore").write_text(".katana/index/\n")
    subprocess.run(["git", "-C", d, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"],
                   check=True, capture_output=True)
    return d


def _make_store(data_root: str, embedding_client=None) -> WikiStore:
    if embedding_client is None:
        embedding_client = _search.FakeEmbeddingClient(dim=512)
    return WikiStore(data_root, embedding_client=embedding_client)


def _sample_frontmatter(**overrides) -> dict:
    fm = {
        "创建日期": "2026-07-30",
        "tags": ["test", "wiki"],
        "类型": "卡片",
        "source_type": "human",
        "credibility": "high",
        "摘要": "测试页面",
        "sources": ["test-source"],
    }
    fm.update(overrides)
    return fm


def _sample_body(title: str = "测试页面") -> str:
    return f"# {title}\n\n这是一个测试页面。\n\n## References\n\n- test-source\n\n相关 [[其他页面]]"


def _commit_count(data_root: str) -> int:
    return int(subprocess.run(
        ["git", "-C", data_root, "rev-list", "--count", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip())


def _manifest_count(data_root: str) -> int:
    return len(list((Path(data_root) / ".katana" / "manifests").glob("*.json")))


# ── A1: write tool three-state tests ────────────────────────────────────────

class TestWriteThreeState:
    """A1: Each write tool: validation reject / success / manifest+commit."""

    def test_create_reject_missing_frontmatter(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_create("测试", "body", {"tags": ["t"]})
        assert "code" in result
        assert result["code"] == "VALIDATION_FAILED"

    def test_create_reject_invalid_title(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_create("test/path", _sample_body("test/path"),
                                    _sample_frontmatter())
        assert result["code"] == "VALIDATION_FAILED"

    def test_create_reject_id_in_frontmatter(self):
        d = _make_data_root()
        store = _make_store(d)
        fm = _sample_frontmatter()
        fm["id"] = "w-000001"
        result = store.wiki_create("测试", _sample_body("测试"), fm)
        assert result["code"] == "VALIDATION_FAILED"

    def test_create_success(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_create("测试页面", _sample_body("测试页面"),
                                    _sample_frontmatter())
        assert "id" in result
        assert result["id"].startswith("w-")
        assert result["path"] == "pages/测试页面.md"
        assert "commit" in result
        assert "manifest" in result

    def test_create_title_exists(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        result = store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        assert result["code"] == "TITLE_EXISTS"
        assert "existing_id" in result

    def test_create_manifest_and_commit(self):
        d = _make_data_root()
        store = _make_store(d)
        commits_before = _commit_count(d)
        manifests_before = _manifest_count(d)
        result = store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        commits_after = _commit_count(d)
        manifests_after = _manifest_count(d)
        assert commits_after == commits_before + 1, "exactly +1 commit per mutation"
        assert manifests_after == manifests_before + 1, "exactly +1 manifest per mutation"
        manifests = list((Path(d) / ".katana" / "manifests").glob("*.json"))
        manifest = json.loads(manifests[-1].read_text())
        assert manifest["tool"] == "wiki_create"
        assert "pages/测试.md" in manifest["changed_paths"]

    def test_update_success(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        new_body = _sample_body("测试") + "\n新增内容。"
        result = store.wiki_update("测试", new_body, _sample_frontmatter())
        assert "id" in result
        assert "commit" in result

    def test_update_not_found(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_update("nonexistent", "body", _sample_frontmatter())
        assert result["code"] == "NOT_FOUND"

    def test_update_id_mismatch(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        fm = _sample_frontmatter()
        fm["id"] = "w-000000"
        result = store.wiki_update("测试", _sample_body("测试"), fm)
        assert result["code"] == "REF_MISMATCH"

    def test_edit_success(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        result = store.wiki_edit("测试", "测试页面", "新测试页面")
        assert "id" in result
        assert "commit" in result

    def test_edit_old_string_not_unique(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试", "test test test [[其他]]", _sample_frontmatter())
        result = store.wiki_edit("测试", "test", "new")
        assert result["code"] == "VALIDATION_FAILED"

    def test_edit_old_string_not_found(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        result = store.wiki_edit("测试", "zzznotfound", "new")
        assert result["code"] == "VALIDATION_FAILED"


# ── A2: rename regression ───────────────────────────────────────────────────

class TestRename:
    """A2: rename rewrites links, old title returns NOT_FOUND, new title reachable."""

    def test_rename_success(self):
        d = _make_data_root()
        store = _make_store(d)
        page_a = store.wiki_create("页面A", _sample_body("页面A"), _sample_frontmatter())
        page_b = store.wiki_create("页面B", "正文 [[页面A]]", _sample_frontmatter(
            title="页面B", 摘要="页面B"))
        result = store.wiki_rename("页面A", "页面A新")
        assert "id" in result
        assert result.get("old_title") == "页面A"
        assert result.get("new_title") == "页面A新"

        get_old = store.wiki_get("页面A")
        assert get_old["code"] == "NOT_FOUND"

        get_new = store.wiki_get("页面A新")
        assert get_new["id"] == page_a["id"]

        body_b = store.wiki_get("页面B")
        assert "[[页面A新]]" in body_b["body"]

    def test_rename_with_alias_links(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面A", _sample_body("页面A"), _sample_frontmatter())
        store.wiki_create("页面B", "正文 [[页面A|别名]]", _sample_frontmatter(
            title="页面B", 摘要="页面B"))
        result = store.wiki_rename("页面A", "页面A新")
        assert "id" in result

        body_b = store.wiki_get("页面B")
        assert "[[页面A新|别名]]" in body_b["body"]

    def test_rename_no_broken_links(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面A", _sample_body("页面A"), _sample_frontmatter())
        store.wiki_create("页面B", "正文 [[页面A]]", _sample_frontmatter(
            title="页面B", 摘要="页面B"))
        store.wiki_rename("页面A", "页面A新")

        body_b = store.wiki_get("页面B")
        assert "[[页面A]]" not in body_b["body"]
        assert "[[页面A]]" not in body_b["body"], "broken link increment must be 0"

    def test_rename_to_existing_title_rejected(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面A", _sample_body("页面A"), _sample_frontmatter())
        store.wiki_create("页面B", _sample_body("页面B"), _sample_frontmatter())
        result = store.wiki_rename("页面A", "页面B")
        assert result["code"] == "TITLE_EXISTS"


# ── A3: delete regression ───────────────────────────────────────────────────

class TestDelete:
    """A3: delete blocked by inlinks, force+remove_links works."""

    def test_delete_blocked_by_inlinks(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面A", _sample_body("页面A"), _sample_frontmatter())
        store.wiki_create("页面B", "正文 [[页面A]]", _sample_frontmatter(
            title="页面B", 摘要="页面B"))
        result = store.wiki_delete("页面A")
        assert result["code"] == "DELETE_BLOCKED"
        assert "页面B" in result["inlinks"]

    def test_delete_force_remove_links(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面A", _sample_body("页面A"), _sample_frontmatter())
        store.wiki_create("页面B", "正文 [[页面A]]", _sample_frontmatter(
            title="页面B", 摘要="页面B"))
        commits_before = _commit_count(d)
        result = store.wiki_delete("页面A", force=True, inlink_action="remove_links")
        commits_after = _commit_count(d)
        assert "id" in result
        assert commits_after == commits_before + 1, "delete + inlink rewrite must land in same commit"

        get_a = store.wiki_get("页面A")
        assert get_a["code"] == "NOT_FOUND"

        body_b = store.wiki_get("页面B")
        assert "页面A" in body_b["body"]
        assert "[[页面A]]" not in body_b["body"]

    def test_delete_force_without_action_rejected(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面A", _sample_body("页面A"), _sample_frontmatter())
        store.wiki_create("页面B", "正文 [[页面A]]", _sample_frontmatter(
            title="页面B", 摘要="页面B"))
        result = store.wiki_delete("页面A", force=True)
        assert result["code"] == "VALIDATION_FAILED"


# ── A4: INV-1, INV-2 tests ──────────────────────────────────────────────────

class TestInvariants:
    """A4: INV-1 (no mutating fs_*), INV-2 (id immutability)."""

    def test_inv1_no_mutating_fs_tools(self):
        import inspect
        from katana_wiki_v2_mcp import server
        tool_names = set()
        for name, obj in inspect.getmembers(server):
            if hasattr(obj, "__mcp_tool__") or name.startswith("fs_"):
                if callable(obj):
                    tool_names.add(name)
        forbidden = {"fs_write", "fs_create", "fs_edit", "fs_copy", "fs_rename",
                     "fs_delete", "fs_batch"}
        assert not (tool_names & forbidden), f"mutating fs tools found: {tool_names & forbidden}"

    def test_inv2_id_server_issued(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        assert result["id"].startswith("w-")
        assert len(result["id"]) == 8

    def test_inv2_id_falsification_rejected(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试", _sample_body("测试"), _sample_frontmatter())
        fm = _sample_frontmatter()
        fm["id"] = "w-000000"
        result = store.wiki_update("测试", _sample_body("测试"), fm)
        assert result["code"] == "REF_MISMATCH"

    def test_inv2_create_with_id_rejected(self):
        d = _make_data_root()
        store = _make_store(d)
        fm = _sample_frontmatter()
        fm["id"] = "w-000001"
        result = store.wiki_create("测试", _sample_body("测试"), fm)
        assert result["code"] == "VALIDATION_FAILED"


# ── A5: search tests ────────────────────────────────────────────────────────

class TestSearch:
    """A5: hybrid search with fake/error embedder."""

    def test_fake_embedder_hybrid(self):
        d = _make_data_root()
        embedder = _search.FakeEmbeddingClient(dim=512)
        store = _make_store(d, embedding_client=embedder)
        store.wiki_create("测试搜索", _sample_body("测试搜索"), _sample_frontmatter(
            title="测试搜索", 摘要="搜索测试"))
        result = store.wiki_search("测试")
        assert len(result["results"]) > 0, "hybrid search must return at least one result"
        health = result["index_health"]
        assert health["mode"] == "hybrid"

    def test_error_embedder_keyword_only(self):
        d = _make_data_root()
        embedder = _search.ErrorEmbeddingClient("service down")
        store = _make_store(d, embedding_client=embedder)
        result = store.wiki_create("测试降级", _sample_body("测试降级"), _sample_frontmatter(
            title="测试降级", 摘要="降级测试"))
        assert "id" in result
        health = store.search_engine.index_health()
        assert health["mode"] == "keyword_only"
        assert "service down" in (health["last_error"] or "")
        assert result["id"] in health["degraded_pages"], "page must be marked degraded"

    def test_error_embedder_write_succeeds(self):
        d = _make_data_root()
        embedder = _search.ErrorEmbeddingClient("service down")
        store = _make_store(d, embedding_client=embedder)
        result = store.wiki_create("测试降级写", _sample_body("测试降级写"), _sample_frontmatter(
            title="测试降级写", 摘要="降级写"))
        assert "id" in result
        assert "commit" in result

        get_result = store.wiki_get(result["id"])
        assert get_result["id"] == result["id"]

    def test_search_snippet(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("搜索测试", _sample_body("搜索测试"), _sample_frontmatter(
            title="搜索测试", 摘要="搜索测试"))
        result = store.wiki_search("搜索")
        if result["results"]:
            assert "snippet" in result["results"][0]


# ── A6: INV-6 bad page isolation ────────────────────────────────────────────

class TestBadPageIsolation:
    """A6: bad frontmatter page doesn't block other operations."""

    def test_bad_page_isolated(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("正常页", _sample_body("正常页"), _sample_frontmatter(
            title="正常页", 摘要="正常"))
        bad_path = Path(d) / "pages" / "坏页.md"
        bad_path.write_text("no frontmatter at all, just text")
        subprocess.run(["git", "-C", d, "add", "pages/坏页.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "add bad page"],
                       check=True, capture_output=True)

        result = store.wiki_create("另一页", _sample_body("另一页"), _sample_frontmatter(
            title="另一页", 摘要="另一页"))
        assert "id" in result

        search_result = store.wiki_search("正常")
        assert "index_health" in search_result

    def test_bad_page_title_conflict_prevented(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("正常页", _sample_body("正常页"), _sample_frontmatter(
            title="正常页", 摘要="正常"))
        bad_path = Path(d) / "pages" / "坏页.md"
        bad_path.write_text("no frontmatter at all, just text")
        subprocess.run(["git", "-C", d, "add", "pages/坏页.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "add bad page"],
                       check=True, capture_output=True)

        result = store.wiki_create("坏页", _sample_body("坏页"), _sample_frontmatter(
            title="坏页", 摘要="坏页"))
        assert result["code"] == "TITLE_EXISTS", "should detect bad page as title conflict"


# ── A7: migration tests ─────────────────────────────────────────────────────

class TestMigration:
    """A7: migration CLI with fixture."""

    def _make_v1_repo(self, base_dir: str) -> str:
        src = Path(base_dir) / "v1_source"
        zettel = src / "Zettelkasten"
        zettel.mkdir(parents=True)
        index_dir = zettel / "Index"
        index_dir.mkdir()
        audit_dir = zettel / ".audit"
        audit_dir.mkdir()
        (audit_dir / "hidden.md").write_text("hidden", encoding="utf-8")

        (zettel / "测试页面.md").write_text(
            "---\n创建日期: 2026-01-01\ntags: [test]\n类型: 卡片\nsource_type: human\n"
            "credibility: high\n摘要: 测试\nsources: [src]\n---\n\n# 测试\n\n正文 [[Index/机器学习]]\n",
            encoding="utf-8")
        (index_dir / "机器学习.md").write_text(
            "---\n创建日期: 2026-01-01\ntags: [ml]\n类型: 索引\nsource_type: human\n"
            "credibility: high\n摘要: ML\nsources: [src]\n---\n\n# ML\n\n正文 [[Zettelkasten/测试页面|测试]]\n",
            encoding="utf-8")
        (zettel / "有ID的页.md").write_text(
            "---\nid: w-abc123\n创建日期: 2026-01-01\ntags: [test]\n类型: 卡片\n---\n\n# 有ID\n\n正文\n",
            encoding="utf-8")

        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "test@test"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "Test"],
                       check=True, capture_output=True)
        (src / "WIKI.md").write_text("# WIKI.md\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "commit", "-m", "init"],
                       check=True, capture_output=True)
        return str(src)

    def test_migrate_flat_structure(self):
        base = tempfile.mkdtemp(prefix="migrate_test_")
        src = self._make_v1_repo(base)
        dest = Path(base) / "v2_dest"

        from katana_wiki_v2_mcp.migrate import migrate
        result = migrate(src, str(dest), dry_run=False)
        assert result["success"]

        pages_dir = dest / "pages"
        assert (pages_dir / "测试页面.md").is_file()
        assert (pages_dir / "机器学习.md").is_file()
        assert not (pages_dir / "Index").exists()

    def test_migrate_link_normalization(self):
        base = tempfile.mkdtemp(prefix="migrate_test_")
        src = self._make_v1_repo(base)
        dest = Path(base) / "v2_dest"

        from katana_wiki_v2_mcp.migrate import migrate
        result = migrate(src, str(dest), dry_run=False)
        assert result["success"]

        body = (dest / "pages" / "测试页面.md").read_text(encoding="utf-8")
        assert "[[机器学习]]" in body
        assert "[[Index/机器学习]]" not in body

    def test_migrate_id_preserved(self):
        base = tempfile.mkdtemp(prefix="migrate_test_")
        src = self._make_v1_repo(base)
        dest = Path(base) / "v2_dest"

        from katana_wiki_v2_mcp.migrate import migrate
        result = migrate(src, str(dest), dry_run=False)
        assert result["success"]

        body = (dest / "pages" / "有ID的页.md").read_text(encoding="utf-8")
        assert "w-abc123" in body

    def test_migrate_excluded_dirs(self):
        base = tempfile.mkdtemp(prefix="migrate_test_")
        src = self._make_v1_repo(base)
        dest = Path(base) / "v2_dest"

        from katana_wiki_v2_mcp.migrate import migrate
        result = migrate(src, str(dest), dry_run=False)
        assert result["success"]

        assert not (dest / "pages" / "hidden.md").exists()

    def test_migrate_conflict_detection(self):
        base = tempfile.mkdtemp(prefix="migrate_test_")
        src = Path(base) / "v1_source"
        zettel = src / "Zettelkasten"
        zettel.mkdir(parents=True)
        (zettel / "Index").mkdir(parents=True)
        (zettel / "Index" / "冲突.md").write_text("---\ntags: [t]\n类型: 卡片\n---\n\nA\n", encoding="utf-8")
        (zettel / "冲突.md").write_text("---\ntags: [t]\n类型: 卡片\n---\n\nB\n", encoding="utf-8")
        subprocess.run(["git", "init", str(src)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.email", "test@test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(src), "commit", "-m", "init"], check=True, capture_output=True)

        dest = Path(base) / "v2_dest"
        from katana_wiki_v2_mcp.migrate import migrate
        result = migrate(str(src), str(dest), dry_run=False)
        assert not result["success"]
        assert result["conflict_report"]["conflict_count"] >= 1

    def test_migrate_idempotent(self):
        base = tempfile.mkdtemp(prefix="migrate_test_")
        src = self._make_v1_repo(base)
        dest = Path(base) / "v2_dest"

        from katana_wiki_v2_mcp.migrate import migrate
        r1 = migrate(src, str(dest), dry_run=False)
        assert r1["success"]

        dest2 = Path(base) / "v2_dest2"
        r2 = migrate(src, str(dest2), dry_run=False)
        assert r2["success"]

        pages1 = sorted((dest / "pages").iterdir())
        pages2 = sorted((dest2 / "pages").iterdir())
        assert len(pages1) == len(pages2)
        for p1, p2 in zip(pages1, pages2):
            assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8")

    def test_migrate_output_openable_by_v2_server(self):
        base = tempfile.mkdtemp(prefix="migrate_test_")
        src = self._make_v1_repo(base)
        dest = Path(base) / "v2_dest"

        from katana_wiki_v2_mcp.migrate import migrate
        result = migrate(src, str(dest), dry_run=False)
        assert result["success"]

        store = _make_store(str(dest))
        store.rebuild_index()

        search_result = store.wiki_search("测试")
        assert "index_health" in search_result
        assert len(search_result["results"]) > 0, "search must return at least one result"

        get_result = store.wiki_get("测试页面")
        assert get_result["id"] is not None


# ── A8: concurrent write serialization ──────────────────────────────────────

class TestConcurrency:
    """A8: concurrent mutations don't produce cross-contamination."""

    def test_concurrent_edit_rename_no_cross_contamination(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("并发页", _sample_body("并发页"), _sample_frontmatter(
            title="并发页", 摘要="并发测试"))

        errors = []
        results = []

        def edit_page():
            try:
                result = store.wiki_edit("并发页", "并发页", "并发页编辑后")
                results.append(("edit", result))
            except Exception as e:
                errors.append(str(e))

        def rename_page():
            try:
                result = store.wiki_rename("并发页", "并发页新")
                results.append(("rename", result))
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=edit_page)
        t2 = threading.Thread(target=rename_page)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"concurrent errors: {errors}"

        get_by_id = store.wiki_get("并发页新")
        if get_by_id.get("code") != "NOT_FOUND":
            assert "并发页编辑后" in get_by_id["body"], \
                "rename must not silently overwrite the edit"

    def test_concurrent_writes_serialized(self):
        d = _make_data_root()
        store = _make_store(d)

        errors = []
        ids = []

        def create_page(title):
            try:
                result = store.wiki_create(title, _sample_body(title),
                                          _sample_frontmatter(title=title, 摘要=title))
                if "id" in result:
                    ids.append(result["id"])
                else:
                    errors.append(result)
            except Exception as e:
                errors.append(str(e))

        commits_before = _commit_count(d)
        manifests_before = _manifest_count(d)

        threads = []
        for i in range(5):
            t = threading.Thread(target=create_page, args=(f"并发页{i}",))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"errors: {errors}"
        assert len(ids) == 5

        commits_after = _commit_count(d)
        manifests_after = _manifest_count(d)
        assert commits_after == commits_before + 5, \
            f"exactly one commit per mutation, expected +5 got +{commits_after - commits_before}"
        assert manifests_after == manifests_before + 5, \
            f"exactly one manifest per mutation, expected +5 got +{manifests_after - manifests_before}"


# ── pages.py unit tests ─────────────────────────────────────────────────────

class TestPages:
    def test_parse_page(self):
        text = "---\nkey: value\n---\nbody"
        fm, body = _pages.parse_page(text)
        assert fm == {"key": "value"}
        assert body == "body"

    def test_parse_no_frontmatter(self):
        text = "just body"
        fm, body = _pages.parse_page(text)
        assert fm == {}
        assert body == "just body"

    def test_render_page(self):
        fm = {"key": "value"}
        body = "body"
        rendered = _pages.render_page(fm, body)
        assert "---\nkey: value\n---\nbody" in rendered

    def test_validate_title(self):
        assert _pages.validate_title("valid") is None
        assert _pages.validate_title(" leading") is not None
        assert _pages.validate_title("trailing ") is not None
        assert _pages.validate_title("has/slash") is not None
        assert _pages.validate_title("has\nnewline") is not None

    def test_extract_wikilinks(self):
        body = "[[A]] and [[B|alias]]"
        links = _pages.extract_wikilinks(body)
        assert "A" in links
        assert "B|alias" in links

    def test_extract_wikilinks_with_aliases(self):
        body = "[[A]] and [[B|alias]]"
        links = _pages.extract_wikilinks_with_aliases(body)
        assert ("A", None) in links
        assert ("B", "alias") in links

    def test_rewrite_wikilinks(self):
        body = "[[A]] and [[A|alias]]"
        new_body = _pages.rewrite_wikilinks(body, "A", "B")
        assert "[[B]]" in new_body
        assert "[[B|alias]]" in new_body

    def test_remove_wikilinks_for_title(self):
        body = "[[A]] and [[A|alias]]"
        new_body = _pages.remove_wikilinks_for_title(body, "A")
        assert "[[A]]" not in new_body
        assert "alias" in new_body

    def test_make_id(self):
        id1 = _pages.make_id()
        id2 = _pages.make_id()
        assert id1 != id2
        assert id1.startswith("w-")
        assert len(id1) == 8

    def test_make_id_deterministic(self):
        id1 = _pages.make_id(seed="test")
        id2 = _pages.make_id(seed="test")
        assert id1 == id2


# ── invariants.py unit tests ────────────────────────────────────────────────

class TestInvariantsUnit:
    def test_validate_page_ingest_grade(self):
        fm = _sample_frontmatter()
        body = _sample_body()
        errs = _inv.validate_page(fm, body, require_summary=True, require_sources=True)
        assert errs == []

    def test_validate_page_missing_fields(self):
        fm = {"tags": ["t"]}
        body = "body"
        errs = _inv.validate_page(fm, body, require_summary=True, require_sources=True)
        assert len(errs) > 0

    def test_validate_page_no_outlink(self):
        fm = _sample_frontmatter()
        body = "no wikilinks here"
        errs = _inv.validate_page(fm, body, require_summary=True, require_sources=True)
        assert any("outlink" in e for e in errs)

    def test_validate_edit_grade_id_immutable(self):
        old_fm = {"id": "w-000001", "创建日期": "2026-01-01", "tags": ["t"],
                  "类型": "卡片", "source_type": "human", "credibility": "high"}
        new_fm = {"id": "w-000002", "创建日期": "2026-01-01", "tags": ["t"],
                  "类型": "卡片", "source_type": "human", "credibility": "high"}
        errs = _inv.validate_edit_grade(old_fm, "old", new_fm, "new")
        assert any("id" in e for e in errs)


# ── query.py tests ──────────────────────────────────────────────────────────

class TestQuery:
    def test_do_query_empty(self):
        def search_fn(q, top_k=10):
            return {"results": [], "index_health": {}}
        log_lines = []
        def log_fn(line):
            log_lines.append(line)
        result = _query._do_query("test", search_fn=search_fn, log_fn=log_fn,
                                  now_fn=lambda: "2026-01-01 00:00")
        assert result["cold"] is True
        assert len(log_lines) == 1

    def test_do_query_with_results(self):
        def search_fn(q, top_k=10):
            return {"results": [{"id": "w-000001", "score": 0.9, "title": "Test",
                                 "snippet": "content"}], "index_health": {}}
        log_lines = []
        result = _query._do_query("test", search_fn=search_fn, log_fn=lambda x: None,
                                  now_fn=lambda: "2026-01-01 00:00")
        assert result["cold"] is False
        assert len(result["candidates"]) == 1
        assert "synthesis_contract" in result


# ── VFS tests ───────────────────────────────────────────────────────────────

class TestVFS:
    def test_fs_read(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        vfs_result = _vfs.fs_read(d, "pages/页面.md")
        assert "content" in vfs_result

    def test_fs_list(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        vfs_result = _vfs.fs_list(d, "pages")
        assert len(vfs_result["entries"]) >= 1

    def test_fs_glob(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        vfs_result = _vfs.fs_glob(d, "pages/*.md")
        assert len(vfs_result["hits"]) >= 1

    def test_fs_stat(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        vfs_result = _vfs.fs_stat(d, "pages/页面.md")
        assert vfs_result["node_type"] == "file"


# ── Store read tests ────────────────────────────────────────────────────────

class TestStoreRead:
    def test_wiki_get_by_id(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        get_result = store.wiki_get(result["id"])
        assert get_result["title"] == "页面"

    def test_wiki_get_by_title(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        get_result = store.wiki_get("页面")
        assert get_result["title"] == "页面"

    def test_wiki_get_not_found(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_get("nonexistent")
        assert result["code"] == "NOT_FOUND"

    def test_wiki_read(self):
        d = _make_data_root()
        store = _make_store(d)
        body = "line1\nline2\nline3\n\n[[其他]]"
        store.wiki_create("页面", body, _sample_frontmatter(title="页面", 摘要="页面"))
        result = store.wiki_read("页面", offset=1, limit=2)
        assert "line1" in result["rendered"]
        assert result["total_lines"] == 5
        assert result["offset"] == 1
        assert result["limit"] == 2

    def test_wiki_list(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("A", _sample_body("A"), _sample_frontmatter(title="A", 摘要="A"))
        store.wiki_create("B", _sample_body("B"), _sample_frontmatter(title="B", 摘要="B"))
        result = store.wiki_list()
        assert len(result["items"]) >= 2

    def test_wiki_list_prefix(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("测试A", _sample_body("测试A"), _sample_frontmatter(
            title="测试A", 摘要="A"))
        store.wiki_create("其他", _sample_body("其他"), _sample_frontmatter(
            title="其他", 摘要="其他"))
        result = store.wiki_list(prefix="测试")
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "测试A"


# ── meta write tests ────────────────────────────────────────────────────────

class TestMetaWrite:
    def test_wiki_meta_write(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_meta_write("WIKI.md", "# WIKI\n\nschema")
        assert "commit" in result

    def test_wiki_meta_write_invalid_name(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_meta_write("invalid.md", "content")
        assert result["code"] == "VALIDATION_FAILED"


# ── report gap test ─────────────────────────────────────────────────────────

class TestReportGap:
    def test_report_gap(self):
        d = _make_data_root()
        store = _make_store(d)
        result = store.wiki_report_gap("test question", note="note")
        assert "commit" in result
        log_content = (Path(d) / "log.md").read_text(encoding="utf-8")
        assert "test question" in log_content
        assert "note" in log_content


# ── ingest plan/apply test ──────────────────────────────────────────────────

class TestIngest:
    def test_ingest_plan(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("已有页", _sample_body("已有页"), _sample_frontmatter(
            title="已有页", 摘要="已有"))
        sources = json.dumps([{
            "title": "新页面",
            "body": _sample_body("新页面"),
            "frontmatter": {
                "创建日期": "2026-07-30",
                "tags": ["test"],
                "类型": "卡片",
                "source_type": "human",
                "credibility": "high",
                "摘要": "新页面",
                "sources": ["test"],
            },
        }])
        result = store.wiki_ingest_plan(sources)
        assert "pages" in result
        assert "base_sha" in result
        assert len(result["pages"]) == 1
        assert result["pages"][0]["title"] == "新页面"
        assert result["pages"][0]["action"] == "create"

    def test_ingest_apply_roundtrip(self):
        d = _make_data_root()
        store = _make_store(d)
        sources = json.dumps([{
            "title": "批量页",
            "body": _sample_body("批量页"),
            "frontmatter": {
                "创建日期": "2026-07-30",
                "tags": ["test"],
                "类型": "卡片",
                "source_type": "human",
                "credibility": "high",
                "摘要": "批量",
                "sources": ["test"],
            },
        }])
        plan = store.wiki_ingest_plan(sources)
        result = store.wiki_ingest_apply(plan)
        assert "commit" in result
        assert "results" in result
        get_result = store.wiki_get("批量页")
        assert get_result["id"] is not None


# ── rebuild index test ──────────────────────────────────────────────────────

class TestRebuildIndex:
    def test_rebuild_index(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        result = store.rebuild_index()
        assert result["pages_indexed"] >= 1


# ── INV-5: clean worktree after mutations ───────────────────────────────────

class TestInv5CleanWorktree:
    def test_clean_worktree_after_mutation(self):
        d = _make_data_root()
        store = _make_store(d)
        store.wiki_create("页面", _sample_body("页面"), _sample_frontmatter(
            title="页面", 摘要="页面"))
        status = subprocess.run(
            ["git", "-C", d, "status", "--porcelain"],
            capture_output=True, text=True
        ).stdout.strip()
        assert status == ""