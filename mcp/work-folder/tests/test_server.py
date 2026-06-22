import katana_work_folder_mcp.server as srv
from katana_kb_mcp_shared import vault_search as vs


def test_compute_scope_whole_vault_when_equal():
    assert srv.compute_scope("/kb", "/kb") is None


def test_compute_scope_subdir():
    assert srv.compute_scope("/kb/智元工作/工作记录", "/kb") == "智元工作/工作记录"


def test_do_search_passes_scope_and_shapes_results(monkeypatch):
    captured = {}

    def fake_search(query, *, top_k=10, dir=None, exclude=None, base_url=vs.DEFAULT_BASE_URL, client=None):
        captured["query"] = query
        captured["top_k"] = top_k
        captured["dir"] = dir
        return vs.SearchResponse(
            results=[vs.SearchResult(path="智元工作/工作记录/foo.md", score=0.85, title="Foo", snippet="bar")],
            mode="hybrid",
        )

    monkeypatch.setattr(srv.vault_search, "search", fake_search)
    out = srv._do_search("工作记录", 5, "智元工作/工作记录")
    assert captured == {"query": "工作记录", "top_k": 5, "dir": "智元工作/工作记录"}
    assert out == [{"path": "智元工作/工作记录/foo.md", "score": 0.85, "title": "Foo", "snippet": "bar"}]


def test_configure_sets_scope():
    srv.configure("/kb/智元工作/工作记录", "/kb")
    assert srv._scope == "智元工作/工作记录"
    srv.configure("/kb", "/kb")
    assert srv._scope is None
