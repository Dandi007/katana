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


def test_hot_path_ships_support_gate():
    """cold=False 时必须下发支撑性自检契约，并点名 wiki_report_gap。

    分数无法区分真命中与噪声（实测：标题命中 top1≈1.03，自然语言问已覆盖话题
    0.0211~0.0378，与无覆盖噪声 0.0206~0.0341 完全重叠），所以服务端不做阈值裁决，
    改为强制模型逐条自评——但契约必须真的随返回体下发，否则等于没有。
    """
    resp = _resp([vs.SearchResult(path="Zettelkasten/a.md", score=0.0211, title="A", snippet="s")])
    out = query._do_query("自然语言提问", None, "/tmp/x", 10,
                          search_fn=lambda q, **k: resp,
                          log_fn=lambda *a: None,
                          now_fn=lambda: "2026-07-29 10:00")
    assert out["cold"] is False
    gate = out["support_gate"]
    assert "score" in gate and "不得用 score" in gate      # 明确禁止用分数替代阅读
    assert "wiki_report_gap" in gate                       # 判为未覆盖时有可执行动作
    assert out["synthesis_contract"]                       # 既有契约不被顶掉


def test_low_score_candidate_is_still_hot_not_dropped():
    """低分候选不得被服务端擅自判 cold——那会误杀「问得像人话」的真命中。"""
    resp = _resp([vs.SearchResult(path="Zettelkasten/第一性原理.md", score=0.0211, title="第一性原理", snippet="s")])
    out = query._do_query("如何判断一个想法是不是第一性的", None, "/tmp/x", 10,
                          search_fn=lambda q, **k: resp,
                          log_fn=lambda *a: None,
                          now_fn=lambda: "2026-07-29 10:00")
    assert out["cold"] is False
    assert [c["path"] for c in out["candidates"]] == ["Zettelkasten/第一性原理.md"]
