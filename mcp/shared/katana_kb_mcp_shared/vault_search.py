"""vault-search (127.0.0.1:18082) HTTP 客户端。复用既有检索栈，不重造 RRF。"""
from dataclasses import dataclass
import os

import httpx

# 允许 env 覆盖：容器化部署时 127.0.0.1 指向容器自己，够不着宿主上的 vault-search
# （真机演练里 wiki 与 work-folder 的检索因此 Connection refused）。默认值不变，
# 宿主部署零影响。
DEFAULT_BASE_URL = os.environ.get(
    "KATANA_VAULT_SEARCH_URL", "http://127.0.0.1:18082"
)


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
    source_root: str | None = None,
    source_id: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
) -> SearchResponse:
    filt: dict = {}
    if dir:
        filt["dir"] = dir
    if exclude:
        filt["exclude"] = exclude
    if source_root is not None:
        filt["source_root"] = source_root
    if source_id is not None:
        filt["source_id"] = source_id
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
