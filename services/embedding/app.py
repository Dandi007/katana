"""katana embedding —— 共享的无状态向量化服务，OpenAI 兼容。

**共享的是计算面**：进文本、出向量，不持有任何域的数据。有状态的索引归各域自持
（`mcp/search`）。这条线见 docs/constitution/002-data-plane-privacy.md 与
work folder wf-77510c `design-search-decentralization.md`。

选 ONNX Runtime 而不是 TEI：同硬件同模型实测，ORT 6 线程 **53.6 chunk/s**，
TEI CPU 镜像最好 **20.8 chunk/s**（调过线程与 batch），差 2.5 倍；且 ORT 路线
镜像更小、线程数可控。TEI 的单条短 query 延迟更好（p50 5.8ms vs ~20ms），但
本服务的主要负载是回填与写路径批量，吞吐优先。

线程数默认 6 = 物理核数。实测 12 线程（超线程）反而掉到 46.8 chunk/s ——
CPU 密集推理上超线程是负收益，别想当然拉满。
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL_NAME = os.environ.get("KATANA_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
THREADS = int(os.environ.get("KATANA_EMBEDDING_THREADS", "6"))
CACHE_DIR = os.environ.get("FASTEMBED_CACHE_PATH", "/opt/models")
# 单次请求内部再分批，避免超大 batch 把内存顶起来。实测吞吐在 batch≥8 后就平了，
# 分批不损失性能。
INNER_BATCH = int(os.environ.get("KATANA_EMBEDDING_INNER_BATCH", "32"))
MAX_INPUTS = int(os.environ.get("KATANA_EMBEDDING_MAX_INPUTS", "512"))

_model = None
# ORT 内部已按 THREADS 并行；再让多个请求并发进去只会互相抢核（oversubscription）。
# 串行化请求在 CPU 推理上是最优解，不是偷懒。
_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    from fastembed import TextEmbedding  # noqa: PLC0415

    t0 = time.time()
    _model = TextEmbedding(model_name=MODEL_NAME, threads=THREADS, cache_dir=CACHE_DIR)
    # 预热：首次推理有一次性开销，别让它落到第一个真实请求头上
    list(_model.embed(["warmup"]))
    app.state.load_seconds = round(time.time() - t0, 2)
    yield


app = FastAPI(title="katana-embedding", lifespan=lifespan)


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None          # OpenAI 客户端会传；本服务单模型，仅回显
    encoding_format: str | None = None


class EmbeddingItem(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingItem]
    model: str
    usage: dict = Field(default_factory=dict)


def _embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    with _lock:
        for i in range(0, len(texts), INNER_BATCH):
            out.extend([v.tolist() for v in _model.embed(texts[i : i + INNER_BATCH])])
    return out


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
def embeddings(req: EmbeddingRequest) -> EmbeddingResponse:
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    if not texts:
        raise HTTPException(status_code=400, detail="input 不能为空")
    if len(texts) > MAX_INPUTS:
        # 明确拒绝而不是悄悄截断——截断会让调用方拿到少于输入数的向量，
        # 而索引侧是按下标一一对应的，静默截断会造成错位。
        raise HTTPException(
            status_code=413,
            detail=f"一次最多 {MAX_INPUTS} 条，收到 {len(texts)}",
        )
    if any(not isinstance(t, str) for t in texts):
        raise HTTPException(status_code=400, detail="input 必须是字符串或字符串数组")
    vectors = _embed(texts)
    return EmbeddingResponse(
        data=[EmbeddingItem(index=i, embedding=v) for i, v in enumerate(vectors)],
        model=MODEL_NAME,
        # 本服务不做 tokenize 计费，给字符数便于调用方观测规模
        usage={"prompt_chars": sum(len(t) for t in texts), "count": len(texts)},
    )


@app.get("/health")
def health() -> dict:
    """真探活：跑一次推理，而不是只回 200。"""
    try:
        v = _embed(["health"])[0]
    except Exception as exc:  # pragma: no cover - 只在模型坏掉时走到
        raise HTTPException(status_code=503, detail=f"embedding 不可用: {exc}") from exc
    return {
        "ok": True,
        "model": MODEL_NAME,
        "dim": len(v),
        "threads": THREADS,
        "load_seconds": getattr(app.state, "load_seconds", None),
    }
