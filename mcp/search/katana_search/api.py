"""域检索门面 —— MCP server 只跟这一层打交道。

两条纪律体现在这里：

1. **写路径即索引路径。** 索引更新由 `index_document` 在 governed commit **之后**
   调用，进程内完成 —— freshness 成为写路径的属性，而不是一个会死的旁路 watchdog
   （旧共享索引器死了 9 天没人发现，正是因为它是旁路）。
   注意：索引是 gitignored 的 runtime 态，**绝不能进 git 事务的 journal** —— journal
   会做 declared-paths 校验，把索引写进去必然 RollbackSafetyError。故是 post-commit
   best-effort：索引失败不回滚已提交内容（内容是权威，索引是派生物），但要留痕。

2. **降级是显式状态。** 向量臂不可用时 `SearchOutcome.mode` 明确写 "keyword"，并带
   上 embedding 的具体状态，不让调用方以为拿到的是混合检索结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from katana_search.embed import EmbeddingClient, EmbeddingUnavailable, default_client
from katana_search.fusion import rrf_merge
from katana_search.index import SearchIndex, chunk_markdown


@dataclass
class SearchOutcome:
    results: list[dict[str, Any]]
    mode: str                       # "hybrid" | "keyword"
    embedding: dict = field(default_factory=dict)
    degraded_reason: str = ""


class DomainSearch:
    def __init__(self, repo_root: str, embedder: EmbeddingClient | None = None) -> None:
        self.index = SearchIndex(repo_root)
        self.embedder = embedder or default_client()

    # -- 写路径 -------------------------------------------------------------

    def index_document(self, path: str, text: str, force: bool = False) -> dict:
        """给一篇文档建/更新索引。由 MCP 在 commit 之后调用。

        返回簿记信息而不是抛异常给写路径——内容已经提交了，索引失败不该让
        一个成功的写事务在调用方看起来像失败。
        """
        if not force and not self.index.needs_reindex(path, text):
            return {"path": path, "skipped": True, "reason": "hash unchanged"}
        chunks = chunk_markdown(text)
        vectors = None
        degraded = ""
        if chunks:
            try:
                vectors = self.embedder.embed(chunks)
            except EmbeddingUnavailable as exc:
                degraded = str(exc)
        n = self.index.upsert(path, text, vectors)
        return {
            "path": path,
            "chunks": n,
            "vectors": bool(vectors),
            "degraded_reason": degraded,
        }

    def remove_document(self, path: str) -> None:
        self.index.remove(path)

    # -- 读路径 -------------------------------------------------------------

    def search(self, query: str, top_k: int = 10) -> SearchOutcome:
        if top_k <= 0:
            return SearchOutcome(results=[], mode="keyword", embedding=self.embedder.status())

        keyword = self.index.keyword_search(query, top_k)

        vector: list[dict] = []
        mode = "keyword"
        degraded = ""
        if self.index.vec_available:
            try:
                qvec = self.embedder.embed_one(query)
                vector = self.index.vector_search(qvec, top_k)
                mode = "hybrid"
            except EmbeddingUnavailable as exc:
                degraded = str(exc)
        else:
            degraded = "sqlite-vec 未安装，向量面不可用"

        merged = rrf_merge(vector, keyword, query=query, all_paths=self.index.all_paths())
        by_path = {r["path"]: r for r in keyword}
        by_path.update({r["path"]: r for r in vector if r["path"] not in by_path})

        results = [
            {
                "path": path,
                "score": round(score, 6),
                "snippet": (by_path.get(path, {}).get("text", "") or "")[:280],
            }
            for path, score in merged[:top_k]
        ]
        return SearchOutcome(
            results=results,
            mode=mode,
            embedding=self.embedder.status(),
            degraded_reason=degraded,
        )

    def stats(self) -> dict:
        return {**self.index.stats(), "embedding": self.embedder.status()}
