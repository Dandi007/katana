"""v2 wiki search — embedded hybrid search (lancedb + keyword).

Process-local: no external vault-search/vault-indexer dependency.
Embedding client is injectable for testing.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx


class EmbeddingClient:
    def __init__(self, base_url: str, api_key_path: str, model: str, dim: int):
        self.base_url = base_url.rstrip("/")
        self.api_key_path = api_key_path
        self.model = model
        self.dim = dim

    def _api_key(self) -> str:
        if self.api_key_path and os.path.isfile(self.api_key_path):
            return Path(self.api_key_path).read_text().strip()
        return ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        key = self._api_key()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/embeddings",
                json={"input": texts, "model": self.model},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = [d["embedding"] for d in data["data"]]
            return embeddings
        except Exception:
            raise


class FakeEmbeddingClient:
    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        result = []
        for text in texts:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            vec = [0.0] * self.dim
            for i in range(min(self.dim, len(h) * 8)):
                byte_idx = i // 8
                bit_idx = i % 8
                if (h[byte_idx] >> bit_idx) & 1:
                    vec[i] = 1.0 / (self.dim ** 0.5)
            result.append(vec)
        return result


class ErrorEmbeddingClient:
    def __init__(self, error_msg: str = "embedding service unavailable"):
        self.error_msg = error_msg
        self.dim = 512

    def embed(self, texts: list[str]) -> None:
        raise RuntimeError(self.error_msg)


class KeywordIndex:
    def __init__(self):
        self._index: dict[str, set[str]] = {}

    def add(self, page_id: str, text: str) -> None:
        tokens = self._tokenize(text)
        for token in tokens:
            self._index.setdefault(token, set()).add(page_id)

    def remove(self, page_id: str) -> None:
        for token_set in self._index.values():
            token_set.discard(page_id)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        tokens = self._tokenize(query)
        if not tokens:
            return []
        scores: dict[str, float] = {}
        for token in tokens:
            for page_id in self._index.get(token, set()):
                scores[page_id] = scores.get(page_id, 0) + 1
        sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
        max_score = sorted_scores[0][1] if sorted_scores else 1.0
        return [(pid, s / max_score) for pid, s in sorted_scores[:top_k]]

    def _tokenize(self, text: str) -> list[str]:
        tokens: list[str] = []
        for i in range(len(text)):
            for j in range(i + 1, min(i + 4, len(text) + 1)):
                tokens.append(text[i:j])
        return tokens

    def save(self, path: str) -> None:
        data = {k: sorted(v) for k, v in self._index.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load(self, path: str) -> None:
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._index = {k: set(v) for k, v in data.items()}


class WikiSearch:
    def __init__(self, data_root: str, embedding_client: Any = None):
        self._data_root = data_root
        self._index_dir = Path(data_root) / ".katana" / "index"
        self._embedding_client = embedding_client
        self._keyword = KeywordIndex()
        self._degraded_pages: set[str] = set()
        self._last_error: str | None = None
        self._mode = "keyword_only"
        self._table = None
        self._db = None

        if self._embedding_client is not None:
            self._mode = "hybrid"

    def _vector_dim(self) -> int:
        if self._embedding_client is not None and hasattr(self._embedding_client, "dim"):
            return self._embedding_client.dim
        return 512

    def _ensure_lancedb(self):
        if self._db is not None:
            return
        try:
            import lancedb
            self._index_dir.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self._index_dir))
            dim = self._vector_dim()
            if "pages" in self._db.list_tables():
                self._table = self._db.open_table("pages")
            else:
                import pyarrow as pa
                self._table = self._db.create_table("pages", pa.table({
                    "id": pa.array([], type=pa.string()),
                    "vector": pa.array([], type=pa.list_(pa.float32(), dim)),
                }))
        except Exception as e:
            self._last_error = str(e)
            self._mode = "keyword_only"

    def search(self, query: str, top_k: int = 10) -> dict:
        keyword_results = self._keyword.search(query, top_k=top_k * 2)

        vector_results: list[tuple[str, float]] = []
        if self._mode == "hybrid" and self._embedding_client is not None:
            try:
                embeddings = self._embedding_client.embed([query])
                self._ensure_lancedb()
                if self._table is not None and self._table.count_rows() > 0:
                    results = self._table.search(embeddings[0]).limit(top_k * 2).to_list()
                    for r in results:
                        vector_results.append((r["id"], 1.0 - r["_distance"]))
            except Exception as e:
                self._last_error = str(e)
                self._mode = "keyword_only"

        fused = self._rrf_fuse(keyword_results, vector_results, top_k)
        return {
            "results": fused,
            "index_health": {
                "mode": self._mode,
                "degraded_pages": sorted(self._degraded_pages),
                "last_error": self._last_error,
            },
        }

    def _rrf_fuse(
        self,
        keyword: list[tuple[str, float]],
        vector: list[tuple[str, float]],
        top_k: int,
    ) -> list[dict]:
        k = 60
        scores: dict[str, float] = {}
        for rank, (pid, _) in enumerate(keyword):
            scores[pid] = scores.get(pid, 0) + 1.0 / (k + rank + 1)
        for rank, (pid, _) in enumerate(vector):
            scores[pid] = scores.get(pid, 0) + 1.0 / (k + rank + 1)
        sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
        return [{"id": pid, "score": sc, "title": "", "snippet": ""} for pid, sc in sorted_scores[:top_k]]

    def _embed_page(self, page_id: str, text: str) -> None:
        if self._embedding_client is None:
            return
        try:
            embeddings = self._embedding_client.embed([text])
            self._ensure_lancedb()
            if self._table is not None:
                import pyarrow as pa
                self._table.add(pa.table({
                    "id": [page_id],
                    "vector": [embeddings[0]],
                }))
        except Exception as e:
            self._degraded_pages.add(page_id)
            self._last_error = str(e)
            self._mode = "keyword_only"

    def index_page(self, page_id: str, title: str, body: str) -> None:
        self._keyword.add(page_id, f"{title}\n{body}")
        self._embed_page(page_id, f"{title}\n{body}")

    def remove_page(self, page_id: str) -> None:
        self._keyword.remove(page_id)
        if self._table is not None:
            try:
                self._table.delete(f"id = '{page_id}'")
            except Exception as e:
                self._last_error = str(e)

    def index_health(self) -> dict:
        return {
            "mode": self._mode,
            "degraded_pages": sorted(self._degraded_pages),
            "last_error": self._last_error,
        }

    def rebuild(self, pages: list[dict]) -> None:
        self._keyword = KeywordIndex()
        self._degraded_pages = set()
        self._last_error = None
        if self._embedding_client is not None:
            self._mode = "hybrid"
        else:
            self._mode = "keyword_only"

        self._ensure_lancedb()
        if self._table is not None:
            try:
                self._db.drop_table("pages")
                import pyarrow as pa
                dim = self._vector_dim()
                self._table = self._db.create_table("pages", pa.table({
                    "id": pa.array([], type=pa.string()),
                    "vector": pa.array([], type=pa.list_(pa.float32(), dim)),
                }))
            except Exception as e:
                self._last_error = str(e)
                self._mode = "keyword_only"

        for page in pages:
            if page["id"] and not page.get("_error"):
                self.index_page(page["id"], page["title"], page["body"])