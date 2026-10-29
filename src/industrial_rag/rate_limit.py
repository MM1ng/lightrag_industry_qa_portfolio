"""In-process sliding-window rate limiting for the anonymous browser surface.

The public workbench surface (graph reads, document source lookups, KB query,
feedback) is reachable without a Bearer token, so it needs its own admission
control.  Bearer-authenticated traffic is never rate limited here.

The limiter is deliberately in-process and dependency free: it is an abuse
brake, not a distributed quota.  For a multi-replica deployment put a shared
limiter in front of the API; this module then still protects each replica.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

# Bound the number of tracked client identities so a spoofed-source flood cannot
# grow the limiter's memory without limit.  Least recently used identities are
# evicted first.
DEFAULT_MAX_TRACKED_KEYS = 4096

BUCKET_DEFAULT = "default"
BUCKET_FEEDBACK = "feedback"


class AnonymousRateLimiter:
    """Sliding-window limiter keyed by ``(bucket, client identity)``.

    One instance belongs to one application (``app.state``); a module level
    singleton would let two test clients consume each other's budget.

    Args:
        default_limit: Requests allowed per window for the shared bucket. ``0``
            disables limiting for that bucket.
        feedback_limit: Requests allowed per window for the tighter feedback
            bucket. ``None`` falls back to ``default_limit``.
        window_seconds: Sliding-window length in seconds.
        max_keys: Upper bound on tracked ``(bucket, key)`` pairs (LRU eviction).
        clock: Monotonic clock, injectable for tests.
    """

    def __init__(
        self,
        *,
        default_limit: int,
        feedback_limit: int | None = None,
        window_seconds: float = 60.0,
        max_keys: int = DEFAULT_MAX_TRACKED_KEYS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._default_limit = max(0, int(default_limit))
        self._feedback_limit = max(
            0, int(default_limit if feedback_limit is None else feedback_limit)
        )
        self._window_seconds = float(window_seconds)
        self._max_keys = max(1, int(max_keys))
        self._clock = clock or time.monotonic
        self._hits: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        """Whether any bucket actually limits traffic."""
        return self._default_limit > 0 or self._feedback_limit > 0

    def limit_for(self, bucket: str = BUCKET_DEFAULT) -> int:
        """Return the per-window budget for ``bucket``."""
        return self._feedback_limit if bucket == BUCKET_FEEDBACK else self._default_limit

    def allow(self, key: str, *, bucket: str = BUCKET_DEFAULT) -> bool:
        """Record one hit and report whether it fits inside the budget."""
        limit = self.limit_for(bucket)
        if limit <= 0:
            return True
        now = self._clock()
        hits = self._prune(bucket, key, now)
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str, *, bucket: str = BUCKET_DEFAULT) -> int:
        """Seconds until the caller may retry (``0`` when not limited)."""
        limit = self.limit_for(bucket)
        if limit <= 0:
            return 0
        now = self._clock()
        hits = self._prune(bucket, key, now)
        if len(hits) < limit:
            return 0
        remaining = self._window_seconds - (now - hits[0])
        return max(1, math.ceil(remaining))

    def reset(self) -> None:
        """Forget every recorded hit (tests and shutdown)."""
        self._hits.clear()

    def tracked_keys(self) -> int:
        """Number of currently tracked ``(bucket, key)`` pairs."""
        return len(self._hits)

    def _prune(self, bucket: str, key: str, now: float) -> deque[float]:
        cache_key = (bucket, key)
        hits = self._hits.get(cache_key)
        if hits is None:
            hits = deque()
            self._hits[cache_key] = hits
        threshold = now - self._window_seconds
        while hits and hits[0] <= threshold:
            hits.popleft()
        self._hits.move_to_end(cache_key)
        self._evict()
        return hits

    def _evict(self) -> None:
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
