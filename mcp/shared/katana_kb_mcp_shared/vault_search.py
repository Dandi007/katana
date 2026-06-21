"""vault-search (127.0.0.1:18082) HTTP 客户端。复用既有检索栈，不重造 RRF。"""
from dataclasses import dataclass
import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:18082"


@dataclass
class SearchResult:
    path: str
    score: float
    title: str = ""
    snippet: str = ""


@dataclass
class SearchResponse:
    results: list[SearchResult]
    mode: str


_RESULT_FIELDS = ("path", "score", "title", "snippet")


def search(
    query: str,
    *,
    top_k: int = 10,
    dir: str | None = None,
    exclude: list[str] | None = None,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
) -> SearchResponse:
    filt: dict = {}
    if dir:
        filt["dir"] = dir
    if exclude:
        filt["exclude"] = exclude
    body: dict = {"query": query, "top_k": top_k}
    if filt:
        body["filter"] = filt

    owns = client is None
    c = client or httpx.Client(base_url=base_url, timeout=30)
    try:
        # 自建 client 带 base_url → 用相对 /search；外部 client（仅测试用，无 base_url）→ 拼绝对 URL
        url = "/search" if (client is None) else f"{base_url}/search"
        resp = c.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns:
            c.close()

    return SearchResponse(
        results=[
            SearchResult(**{k: r[k] for k in _RESULT_FIELDS if k in r})
            for r in data.get("results", [])
        ],
        mode=data.get("mode", ""),
    )
