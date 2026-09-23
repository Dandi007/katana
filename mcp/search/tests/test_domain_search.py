"""域检索的行为契约。

重点钉三件事，都是设计里明确要保住的性质：
  1. 中文能查得到（trigram tokenizer 是为此选的）
  2. embedding 挂掉时降级为 keyword-only 且**状态可见**，不是查询整个失败
  3. 写完立即可检索（freshness 属于写路径，不依赖任何旁路进程）
"""

from __future__ import annotations

import pytest

from katana_search import DomainSearch, EmbeddingUnavailable, chunk_markdown
from katana_search.embed import EmbeddingClient
from katana_search.fusion import rrf_merge, title_match_boost


class DownEmbedder(EmbeddingClient):
    """永远不可用的 embedding——模拟当前真实状况（端点 502）。"""

    def embed(self, texts):
        raise EmbeddingUnavailable("endpoint down (test)")

    def status(self):
        return {"state": "circuit_open", "endpoint": "test://down", "last_error": "down"}


class FakeEmbedder(EmbeddingClient):
    """确定性假向量：按文本长度铺一个 4 维向量，够做 KNN 排序断言。"""

    def __init__(self):
        super().__init__(endpoint="test://fake")

    def embed(self, texts):
        out = []
        for t in texts:
            n = float(len(t))
            out.append([n, n / 2, 1.0, 0.5])
        return out

    def status(self):
        return {"state": "up", "endpoint": "test://fake"}


@pytest.fixture
def repo(tmp_path):
    return str(tmp_path)


def test_chinese_query_hits(repo):
    """中文检索必须命中——unicode61 不切 CJK，选 trigram 就是为了这条。"""
    ds = DomainSearch(repo, embedder=DownEmbedder())
    ds.index_document("wf-1/design.md", "# 数据面封仓\n\n把 data root 变成 MCP 进程私有成员。\n")
    ds.index_document("wf-2/notes.md", "# 无关内容\n\n今天天气不错，适合散步。\n")
    out = ds.search("数据面", top_k=5)
    paths = [r["path"] for r in out.results]
    assert "wf-1/design.md" in paths
    assert "wf-2/notes.md" not in paths


def test_degrades_to_keyword_with_visible_state(repo):
    """embedding 挂掉 → 仍返回结果，且 mode/embedding 明确暴露降级，不静默。"""
    ds = DomainSearch(repo, embedder=DownEmbedder())
    ds.index_document("a.md", "容器化演练与卷迁移")
    out = ds.search("容器化", top_k=5)
    assert out.results, "降级不等于查不到"
    assert out.mode == "keyword"
    assert out.embedding["state"] == "circuit_open"
    assert out.degraded_reason


def test_indexing_survives_embedding_outage(repo):
    """端点挂掉时索引仍要能推进——内容是权威，向量是派生物。"""
    ds = DomainSearch(repo, embedder=DownEmbedder())
    r = ds.index_document("a.md", "内容照样进索引")
    assert r["chunks"] >= 1
    assert r["vectors"] is False
    assert r["degraded_reason"]


def test_write_is_immediately_searchable(repo):
    """写完即可检索：freshness 属于写路径，不依赖任何旁路 watchdog。"""
    ds = DomainSearch(repo, embedder=DownEmbedder())
    assert ds.search("刚写入的内容", top_k=5).results == []
    ds.index_document("new.md", "刚写入的内容应当立即可以被检索到")
    assert [r["path"] for r in ds.search("刚写入的内容", top_k=5).results] == ["new.md"]


def test_update_and_delete(repo):
    ds = DomainSearch(repo, embedder=DownEmbedder())
    ds.index_document("a.md", "第一版内容关于封仓")
    ds.index_document("a.md", "第二版内容关于检索")
    assert ds.search("封仓", top_k=5).results == [], "旧版内容应已从索引移除"
    assert ds.search("检索", top_k=5).results
    ds.remove_document("a.md")
    assert ds.search("检索", top_k=5).results == []


def test_reindex_skipped_when_hash_unchanged(repo):
    ds = DomainSearch(repo, embedder=DownEmbedder())
    ds.index_document("a.md", "同样的内容")
    r = ds.index_document("a.md", "同样的内容")
    assert r["skipped"] is True


def test_hybrid_mode_when_embedding_up(repo):
    ds = DomainSearch(repo, embedder=FakeEmbedder())
    ds.index_document("a.md", "封仓设计与容器化")
    out = ds.search("封仓", top_k=5)
    assert out.mode == "hybrid", f"向量面可用时应为 hybrid，实际 {out.mode}/{out.degraded_reason}"
    assert out.results


def test_stats_reports_vector_backend(repo):
    ds = DomainSearch(repo, embedder=FakeEmbedder())
    ds.index_document("a.md", "内容")
    s = ds.stats()
    assert s["docs"] == 1
    assert s["vector_backend"] == "sqlite-vec"


# --- 移植自 agent-knowledge 的融合逻辑：行为不得漂移 ---------------------------


def test_title_exact_match_dominates():
    assert title_match_boost("AI时代的测试", "x/AI 时代的测试.md") == 1.0


def test_title_boost_injects_uncandidated_path():
    """标题命中但没进任何候选集的文档也要浮现——旧实现踩过整体漏掉的坑。"""
    merged = rrf_merge([], [], query="封仓设计", all_paths=["a/封仓设计.md", "b/无关.md"])
    assert merged[0][0] == "a/封仓设计.md"


def test_short_note_boost_prefers_atomic_card():
    """同等 RRF 下，浅路径原子卡应排在深路径工作记录之前。"""
    v = [{"path": "card.md"}, {"path": "a/b/c/d/e/doc.md"}]
    merged = rrf_merge(v, [])
    assert merged[0][0] == "card.md"


def test_chunking_merges_short_tail():
    chunks = chunk_markdown("段落一\n\n段落二\n\n短")
    assert all(len(c) >= 3 for c in chunks)
    assert "短" in chunks[-1]


def test_two_char_chinese_query(repo):
    """中文双字词必须能查到。trigram 最小索引单元是 3 字符，实测 '检索' 在含
    「关于检索」的文档上 FTS 命中 0——这条钉住 LIKE 兜底真的接上了。"""
    ds = DomainSearch(repo, embedder=DownEmbedder())
    ds.index_document("a.md", "第二版内容关于检索与封仓")
    ds.index_document("b.md", "完全无关的一段文字")
    for q in ("检索", "封仓", "内容"):
        paths = [r["path"] for r in ds.search(q, top_k=5).results]
        assert "a.md" in paths, f"双字词 {q!r} 查不到"
        assert "b.md" not in paths, f"双字词 {q!r} 误召 b.md"
