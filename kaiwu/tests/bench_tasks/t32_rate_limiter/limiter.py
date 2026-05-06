"""Rate limiter facade combining token bucket and sliding window."""

from token_bucket import TokenBucket, SlidingWindowCounter
from typing import Callable, Any


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded."""
    pass


class PerKeyLimiter:
    """Manages separate rate limiters per key (e.g., per IP or user)."""

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._limiters: dict[str, Any] = {}

    def get_or_create(self, key: str) -> Any:
        if key not in self._limiters:
            self._limiters[key] = self._factory()
        return self._limiters[key]

    def allow(self, key: str, now: float = None) -> bool:
        limiter = self.get_or_create(key)
        return limiter.allow(now)

    def reset(self, key: str) -> None:
        self._limiters.pop(key, None)

    def active_keys(self) -> list[str]:
        return list(self._limiters.keys())


class CompositeRateLimiter:
    """Applies both a token bucket and a sliding window; both must pass."""

    def __init__(self, bucket: TokenBucket, window: SlidingWindowCounter):
        self._bucket = bucket
        self._window = window

    def allow(self, now: float = None) -> bool:
        import time
        if now is None:
            now = time.monotonic()
        bucket_ok = self._bucket.allow(now)
        window_ok = self._window.allow(now)
        return bucket_ok and window_ok
