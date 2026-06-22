from katana_wiki_mcp import query
from katana_kb_mcp_shared import vault_search as vs


def _resp(results):
    return vs.SearchResponse(results=results, mode="hybrid")


def test_hot_returns_candidates_and_contract():
    def fake_search(q, *, top_k=10, dir=None, exclude=None, base_url="", client=None):
        return _resp([vs.SearchResult(path="Zettelkasten/a.md", score=0.9, title="A", snippet="s")])
    logged = []
    out = query._do_query("咖啡", "Zettelkasten", "/wiki", 10,
                          search_fn=fake_search, log_fn=lambda r, l: logged.append((r, l)),
                          now_fn=lambda: "2026-06-22 10:00")
    assert out["cold"] is False
    assert out["candidates"][0]["path"] == "Zettelkasten/a.md"
    assert "inference" in out["synthesis_contract"].lower()
    assert out["candidate_count"] == 1
    assert logged == []  # hot 不写 gap log


def test_cold_writes_gap_log_and_flags():
    def fake_search(q, *, top_k=10, dir=None, exclude=None, base_url="", client=None):
        return _resp([])
    logged = []
    out = query._do_query("不存在的主题", None, "/wiki", 10,
                          search_fn=fake_search, log_fn=lambda r, l: logged.append((r, l)),
                          now_fn=lambda: "2026-06-22 10:00")
    assert out["cold"] is True
    assert out["candidates"] == []
    assert len(logged) == 1
    root, line = logged[0]
    assert root == "/wiki"
    assert "gap: 不存在的主题" in line and "2026-06-22 10:00" in line


def test_search_scoped_to_wiki_root():
    captured = {}
    def fake_search(q, *, top_k=10, dir=None, exclude=None, base_url="", client=None):
        captured["dir"] = dir; captured["top_k"] = top_k
        return _resp([])
    query._do_query("x", "Zettelkasten", "/wiki", 7,
                   search_fn=fake_search, log_fn=lambda r, l: None, now_fn=lambda: "t")
    assert captured == {"dir": "Zettelkasten", "top_k": 7}


def test_server_has_wiki_root_attr():
    import katana_wiki_mcp.server as s
    assert hasattr(s, "_wiki_root")
