"""Tests for the rate limiter system (token_bucket, limiter)."""

import pytest
from token_bucket import TokenBucket, SlidingWindowCounter
from limiter import PerKeyLimiter, CompositeRateLimiter, RateLimitExceeded


class TestTokenBucket:
    def test_initial_full_bucket(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.available_tokens(now=0.0) == 10.0

    def test_allows_up_to_capacity(self):
        bucket = TokenBucket(capacity=5, refill_rate=1.0)
        results = [bucket.allow(now=0.0) for _ in range(5)]
        assert all(results)

    def test_rejects_when_empty(self):
        bucket = TokenBucket(capacity=3, refill_rate=1.0)
        for _ in range(3):
            bucket.allow(now=0.0)
        assert bucket.allow(now=0.0) is False

    def test_refills_over_time(self):
        """After 2 seconds at rate=1.0, should have 2 new tokens."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        for _ in range(10):
            bucket.allow(now=0.0)
        tokens = bucket.available_tokens(now=2.0)
        assert abs(tokens - 2.0) < 0.01

    def test_refill_does_not_exceed_capacity(self):
        bucket = TokenBucket(capacity=5, refill_rate=10.0)
        for _ in range(5):
            bucket.allow(now=0.0)
        tokens = bucket.available_tokens(now=100.0)
        assert tokens == 5.0

    def test_refill_rate_respected_fractional_second(self):
        """Rate=4 tokens/sec: after 0.5 seconds, 2 tokens added."""
        bucket = TokenBucket(capacity=10, refill_rate=4.0)
        for _ in range(10):
            bucket.allow(now=0.0)
        tokens = bucket.available_tokens(now=0.5)
        assert abs(tokens - 2.0) < 0.01

    def test_refill_rate_respected_sub_second(self):
        """Rate=10 tokens/sec: after 0.3 seconds, 3 tokens added."""
        bucket = TokenBucket(capacity=20, refill_rate=10.0)
        for _ in range(20):
            bucket.allow(now=0.0)
        tokens = bucket.available_tokens(now=0.3)
        assert abs(tokens - 3.0) < 0.01


class TestSlidingWindow:
    def test_allows_within_limit(self):
        sw = SlidingWindowCounter(limit=5, window_seconds=1.0)
        results = [sw.allow(now=float(i) * 0.1) for i in range(5)]
        assert all(results)

    def test_rejects_over_limit(self):
        sw = SlidingWindowCounter(limit=3, window_seconds=1.0)
        for _ in range(3):
            sw.allow(now=0.0)
        assert sw.allow(now=0.0) is False

    def test_old_requests_evicted(self):
        """Requests older than window_seconds should not count."""
        sw = SlidingWindowCounter(limit=3, window_seconds=1.0)
        for _ in range(3):
            sw.allow(now=0.0)
        assert sw.allow(now=1.1) is True

    def test_current_count(self):
        sw = SlidingWindowCounter(limit=10, window_seconds=2.0)
        sw.allow(now=0.0)
        sw.allow(now=0.5)
        sw.allow(now=1.0)
        assert sw.current_count(now=1.0) == 3
        assert sw.current_count(now=2.1) == 2

    def test_boundary_exactly_at_window_edge(self):
        """Request at exactly window_seconds ago should be evicted."""
        sw = SlidingWindowCounter(limit=2, window_seconds=1.0)
        sw.allow(now=0.0)
        sw.allow(now=0.5)
        # At t=1.0, the request at t=0.0 is exactly at the boundary and
        # should be evicted (it is no longer within the window).
        assert sw.current_count(now=1.0) == 1

    def test_window_slides_correctly(self):
        sw = SlidingWindowCounter(limit=3, window_seconds=1.0)
        sw.allow(now=0.0)
        sw.allow(now=0.3)
        sw.allow(now=0.6)
        # All 3 slots used; at t=1.0 the first (t=0.0) is evicted
        assert sw.allow(now=1.0) is True


class TestPerKeyLimiter:
    def test_separate_limits_per_key(self):
        limiter = PerKeyLimiter(lambda: TokenBucket(capacity=2, refill_rate=1.0))
        assert limiter.allow("user-1", now=0.0) is True
        assert limiter.allow("user-1", now=0.0) is True
        assert limiter.allow("user-1", now=0.0) is False
        assert limiter.allow("user-2", now=0.0) is True

    def test_reset_clears_limiter(self):
        limiter = PerKeyLimiter(lambda: TokenBucket(capacity=1, refill_rate=0.0))
        limiter.allow("user-1", now=0.0)
        assert limiter.allow("user-1", now=0.0) is False
        limiter.reset("user-1")
        assert limiter.allow("user-1", now=0.0) is True


class TestCompositeRateLimiter:
    def test_both_must_pass(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        window = SlidingWindowCounter(limit=2, window_seconds=1.0)
        composite = CompositeRateLimiter(bucket, window)
        assert composite.allow(now=0.0) is True
        assert composite.allow(now=0.0) is True
        # Window limit hit even though bucket has tokens
        assert composite.allow(now=0.0) is False
