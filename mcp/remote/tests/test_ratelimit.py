"""Tests for rate limiting."""
from katana_remote.ratelimit import RateLimiter, RateLimitConfig


def test_rate_limit_allows_initial_requests():
    config = RateLimitConfig(requests_per_minute=1000)
    limiter = RateLimiter(config)
    for _ in range(100):
        assert limiter.check("alice", "tenant-1")


def test_rate_limit_blocks_excessive():
    config = RateLimitConfig(requests_per_minute=5)
    limiter = RateLimiter(config)
    for _ in range(5):
        assert limiter.check("alice", "tenant-1")
    assert not limiter.check("alice", "tenant-1")


def test_rate_limit_per_principal_tenant():
    config = RateLimitConfig(requests_per_minute=5)
    limiter = RateLimiter(config)
    for _ in range(5):
        assert limiter.check("alice", "tenant-1")
    assert not limiter.check("alice", "tenant-1")
    assert limiter.check("bob", "tenant-1")
    assert limiter.check("alice", "tenant-2")


def test_rate_limit_mutation_count():
    config = RateLimitConfig(mutations_per_minute=3)
    limiter = RateLimiter(config)
    for _ in range(3):
        assert limiter.check("alice", "tenant-1", is_mutation=True)
    assert not limiter.check("alice", "tenant-1", is_mutation=True)


def test_rate_limit_batch():
    config = RateLimitConfig(batch_operations_per_minute=2)
    limiter = RateLimiter(config)
    for _ in range(2):
        assert limiter.check("alice", "tenant-1", is_batch=True)
    assert not limiter.check("alice", "tenant-1", is_batch=True)


def test_rate_limit_minute_reset():
    config = RateLimitConfig(requests_per_minute=5)
    limiter = RateLimiter(config)
    for _ in range(5):
        assert limiter.check("alice", "tenant-1")
    assert not limiter.check("alice", "tenant-1")
    state = limiter._states["alice::tenant-1"]
    state.last_reset = 0
    state.request_count = 0
    assert limiter.check("alice", "tenant-1")