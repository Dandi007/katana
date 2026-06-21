import json
import httpx
from katana_kb_mcp_shared import vault_search as vs


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_builds_request_and_parses():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "results": [
                {"path": "Zettelkasten/a.md", "score": 0.9, "title": "A", "snippet": "..."},
            ],
            "mode": "hybrid",
        })

    resp = vs.search("咖啡 萃取", top_k=5, dir="Zettelkasten", client=_mock_client(handler))

    assert captured["url"].endswith("/search")
    assert captured["body"] == {"query": "咖啡 萃取", "top_k": 5, "filter": {"dir": "Zettelkasten"}}
    assert resp.mode == "hybrid"
    assert resp.results[0].path == "Zettelkasten/a.md"
    assert resp.results[0].score == 0.9


def test_search_omits_empty_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "filter" not in body
        return httpx.Response(200, json={"results": [], "mode": "keyword"})

    vs.search("x", client=_mock_client(handler))


def test_search_passes_exclude():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["filter"] == {"exclude": ["memory"]}
        return httpx.Response(200, json={"results": [], "mode": "hybrid"})

    vs.search("x", exclude=["memory"], client=_mock_client(handler))
