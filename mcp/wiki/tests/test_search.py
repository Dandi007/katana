import katana_wiki_mcp.server as srv
from katana_kb_mcp_shared import vault_search as vs


def test_compute_scope_whole_vault_when_equal():
    assert srv.compute_scope("/vault", "/vault") is None


def test_compute_scope_subdir():
    assert srv.compute_scope("/vault/Zettelkasten", "/vault") == "Zettelkasten"


def test_do_search_passes_scope_and_shapes_results(monkeypatch):
    captured = {}

    def fake_search(query, *, top_k=10, dir=None, exclude=None, base_url=vs.DEFAULT_BASE_URL, client=None):
        captured["query"] = query
        captured["top_k"] = top_k
        captured["dir"] = dir
        return vs.SearchResponse(
            results=[vs.SearchResult(path="Zettelkasten/a.md", score=0.9, title="A", snippet="...")],
            mode="hybrid",
        )

    monkeypatch.setattr(srv.vault_search, "search", fake_search)
    out = srv._do_search("咖啡", 5, "Zettelkasten")
    assert captured == {"query": "咖啡", "top_k": 5, "dir": "Zettelkasten"}
    assert out == [{"path": "Zettelkasten/a.md", "score": 0.9, "title": "A", "snippet": "..."}]


def test_configure_sets_scope(monkeypatch):
    srv.configure("/vault/Zettelkasten", "/vault")
    assert srv._scope == "Zettelkasten"
    srv.configure("/vault", "/vault")
    assert srv._scope is None
