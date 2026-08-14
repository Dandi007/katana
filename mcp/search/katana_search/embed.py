"""共享 embedding API 客户端。

**共享的是无状态的计算面**：进文本、出向量，不持有任何域的数据——与共享一个 LLM
endpoint 同性质。有状态的索引归各域自持（见 index.py）。这条线不划清楚，「各域自持
检索」会被读成「每个域各跑一份 bge」，白烧内存。

降级纪律（继承 agent-knowledge 踩过的坑）：端点不可达时，**不能每次查询都等一次网络
超时**——旧实现实测 ~6s/query，用户只能靠手动置 VAULT_SEARCH_DISABLE_VECTOR 逃生。
这里用熔断：连续失败到阈值就直接短路，冷却期内零网络开销，冷却后自动试探恢复。

降级是**显式状态**（`probe()` / 返回的 mode），不是静默把混合检索退化成关键词检索还
让调用方以为拿到了向量结果。
"""

from __future__ import annotations

import os
import threading
import time

import httpx

DEFAULT_ENDPOINT = "http://172.22.62.133:18081/v1/embeddings"
DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"

_ENV_ENDPOINT = "KATANA_EMBEDDING_ENDPOINT"
_ENV_MODEL = "KATANA_EMBEDDING_MODEL"
_ENV_API_KEY = "KATANA_EMBEDDING_API_KEY"
_ENV_DISABLE = "KATANA_EMBEDDING_DISABLED"

_FALSEY = {"", "0", "false", "no", "off"}

# 熔断参数：连续 N 次失败后短路 COOLDOWN 秒。数值取保守值——embedding 端点
# 恢复不需要秒级发现，而查询路径上多等一次超时是实打实的用户可感延迟。
_FAILURE_THRESHOLD = 2
_COOLDOWN_SECONDS = 120
_TIMEOUT_SECONDS = 5.0


class EmbeddingUnavailable(RuntimeError):
    """向量臂不可用。调用方应降级为 keyword-only，而不是把查询整个失败掉。"""


class EmbeddingClient:
    """线程安全的 embedding 客户端，带熔断。"""

    def __init__(
        self,
        endpoint: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self.endpoint = endpoint or os.environ.get(_ENV_ENDPOINT, DEFAULT_ENDPOINT)
        self.model = model or os.environ.get(_ENV_MODEL, DEFAULT_MODEL)
        self.api_key = api_key if api_key is not None else os.environ.get(_ENV_API_KEY, "")
        self.timeout = timeout
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._last_error = ""

    # -- 状态 ---------------------------------------------------------------

    @property
    def disabled_by_env(self) -> bool:
        return os.environ.get(_ENV_DISABLE, "").strip().lower() not in _FALSEY

    def _circuit_open(self) -> bool:
        return time.monotonic() < self._open_until

    def status(self) -> dict:
        """可观测状态。降级必须看得见，不能只体现为结果变差。"""
        with self._lock:
            if self.disabled_by_env:
                state = "disabled"
            elif self._circuit_open():
                state = "circuit_open"
            else:
                state = "up" if self._consecutive_failures == 0 else "degraded"
            return {
                "state": state,
                "endpoint": self.endpoint,
                "model": self.model,
                "consecutive_failures": self._consecutive_failures,
                "cooldown_remaining_s": max(0.0, round(self._open_until - time.monotonic(), 1)),
                "last_error": self._last_error,
            }

    # -- 调用 ---------------------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。不可用时抛 EmbeddingUnavailable，由调用方降级。"""
        if not texts:
            return []
        if self.disabled_by_env:
            raise EmbeddingUnavailable(f"{_ENV_DISABLE} 已置位")
        with self._lock:
            if self._circuit_open():
                raise EmbeddingUnavailable(
                    f"熔断中（{self.status()['cooldown_remaining_s']}s 后重试）：{self._last_error}"
                )
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        try:
            resp = httpx.post(
                self.endpoint,
                json={"model": self.model, "input": texts},
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            # OpenAI 兼容响应不保证按输入序返回，按 index 排
            ordered = sorted(data, key=lambda d: d.get("index", 0))
            vectors = [d["embedding"] for d in ordered]
            if len(vectors) != len(texts):
                raise ValueError(f"返回 {len(vectors)} 个向量，期望 {len(texts)}")
        except Exception as exc:
            self._record_failure(exc)
            raise EmbeddingUnavailable(str(exc)[:200]) from exc
        self._record_success()
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def probe(self) -> dict:
        """主动探活并刷新状态。给 health 端点与运维用，不在查询路径上调。"""
        try:
            self.embed(["probe"])
        except EmbeddingUnavailable:
            pass
        return self.status()

    # -- 熔断簿记 -----------------------------------------------------------

    def _record_failure(self, exc: Exception) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"[:200]
            if self._consecutive_failures >= _FAILURE_THRESHOLD:
                self._open_until = time.monotonic() + _COOLDOWN_SECONDS

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0
            self._last_error = ""


_default: EmbeddingClient | None = None
_default_lock = threading.Lock()


def default_client() -> EmbeddingClient:
    global _default
    with _default_lock:
        if _default is None:
            _default = EmbeddingClient()
        return _default
