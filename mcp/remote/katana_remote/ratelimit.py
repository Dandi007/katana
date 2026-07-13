"""Rate limiting: per-principal/tenant request, resource, batch, mutation limits.

Design §5.4/§7.2: reject before staging with stable RATE_LIMITED error (retryable).
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class RateLimitConfig:
    requests_per_second: float = 10.0
    requests_per_minute: float = 100.0
    mutations_per_minute: float = 20.0
    batch_operations_per_minute: float = 30.0
    resources_per_second: float = 5.0
    recursive_operations_per_minute: float = 10.0

    bucket_capacity: float = 100.0
    bucket_refill_rate: float = 10.0


@dataclass
class RateLimitState:
    last_reset: float = field(default_factory=time.time)
    request_count: int = 0
    mutation_count: int = 0
    batch_operation_count: int = 0
    resource_count: int = 0
    recursive_operation_count: int = 0
    token_bucket: float = 100.0


class RateLimiter:
    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._states: dict[str, RateLimitState] = defaultdict(RateLimitState)

    def _key(self, principal: str, tenant: str) -> str:
        return f"{principal}::{tenant}"

    def check(self, principal: str, tenant: str,
              is_mutation: bool = False,
              is_batch: bool = False,
              resource_count: int = 0,
              is_recursive: bool = False) -> bool:
        key = self._key(principal, tenant)
        state = self._states[key]
        now = time.time()
        elapsed = now - state.last_reset

        if elapsed >= 1.0:
            state.request_count = 0
            state.resource_count = 0
        if elapsed >= 60.0:
            state.mutation_count = 0
            state.batch_operation_count = 0
            state.recursive_operation_count = 0
            state.last_reset = now

        state.token_bucket = min(
            self._config.bucket_capacity,
            state.token_bucket + self._config.bucket_refill_rate * elapsed,
        )

        if state.token_bucket < 1.0:
            return False

        state.request_count += 1
        if state.request_count > self._config.requests_per_minute:
            return False

        if is_mutation:
            state.mutation_count += 1
            if state.mutation_count > self._config.mutations_per_minute:
                return False

        if is_batch:
            state.batch_operation_count += 1
            if state.batch_operation_count > self._config.batch_operations_per_minute:
                return False

        if resource_count > 0:
            state.resource_count += resource_count
            if state.resource_count > self._config.resources_per_second:
                return False

        if is_recursive:
            state.recursive_operation_count += 1
            if state.recursive_operation_count > self._config.recursive_operations_per_minute:
                return False

        state.token_bucket -= 1.0
        return True


def check_rate_limit(
    limiter: RateLimiter,
    principal: str,
    tenant: str,
    is_mutation: bool = False,
    is_batch: bool = False,
    resource_count: int = 0,
    is_recursive: bool = False,
) -> bool:
    return limiter.check(
        principal, tenant,
        is_mutation=is_mutation,
        is_batch=is_batch,
        resource_count=resource_count,
        is_recursive=is_recursive,
    )