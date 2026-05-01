"""In-process per-IP rate limit for the expensive ``/generate`` route.

A single ``/generate`` request triggers an Anthropic narrative call
costing roughly $0.10-$0.30. Without a cap, one abusive client could
burn through the published Anthropic spend ceiling in minutes — Fly's
own pay-as-you-go tier exposes no hard cap on most accounts, so the
defense lives in the app.

Implementation:

* Sliding-window counter per client IP. Each ``check`` evicts
  expired timestamps from the head of the per-IP deque before
  comparing length against the configured limit.
* Bounded total size — when ``max_keys`` is reached we drop the
  least-recently-inserted IP. Python's dict insertion-order semantics
  give us LRU-by-insert for free, which is good enough to bound RAM
  under a flood of unique source IPs.
* Thread-safe via a single ``threading.Lock`` around the bucket
  map. uvicorn's worker model means concurrent requests can land at
  the same time; the lock hold is microseconds so contention is not
  meaningful at v0 traffic.
* In-process state only. A restart wipes the counters; that's fine
  because the window is short (one hour) and Fly's HA pair sees the
  same client through different machines, leaking at most 2x the
  per-IP cap. Across-replica accuracy would need Redis, which is
  more infrastructure than v0 warrants.

Behind Fly's edge proxy ``request.client.host`` is one of Fly's edge
IPs, identical for all clients. The real client lives in
``Fly-Client-IP`` — :func:`client_ip` reads that first, falling back
to ``X-Forwarded-For`` for non-Fly deployments and finally to the
socket peer for local development.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Final

from fastapi import HTTPException, Request

# Per-IP cap. Picked so a single client can iterate a few times on
# their own memory (3-5 prompt tweaks plus the carousel call) without
# friction, while bounding the worst case to roughly $2/hour of
# Anthropic spend per IP.
GENERATE_LIMIT_PER_HOUR: Final[int] = 10
GENERATE_WINDOW_SECONDS: Final[int] = 60 * 60

# Hard ceiling on the IP map so a flood of unique source IPs cannot
# OOM the process. 10k entries is ~hundreds of KB of deques even at
# the per-IP cap; far below the 512 MB Fly machine.
DEFAULT_MAX_KEYS: Final[int] = 10_000


class RateLimiter:
    """Sliding-window per-key request counter.

    One instance per app, hung off ``app.state.generate_limiter``.
    Tests construct their own with a tiny ``limit`` so the 429 path
    is reachable in a few requests without sleeping.
    """

    __slots__ = ("_buckets", "_limit", "_lock", "_max_keys", "_window")

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max_keys
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """Record a request attempt for ``key`` and return the verdict.

        Returns ``(allowed, retry_after_seconds)``. When ``allowed``
        is ``False``, ``retry_after_seconds`` is the integer-second
        wait until the oldest in-window entry ages out — suitable
        for a ``Retry-After`` header.
        """
        ts = now if now is not None else time.monotonic()
        cutoff = ts - self._window
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:
                    # Drop the LRU-by-insert entry. We re-insert keys
                    # below by ``self._buckets[key] = bucket``, which
                    # does NOT refresh insertion order in Python's
                    # dict — but ``check`` for an existing key
                    # short-circuits the eviction branch entirely, so
                    # the eviction here only fires for genuinely new
                    # keys and the LRU semantics hold.
                    self._buckets.pop(next(iter(self._buckets)))
                bucket = deque()
                self._buckets[key] = bucket
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                # +1 so a client that obeys Retry-After lands just
                # past the boundary instead of bouncing again on the
                # rounding floor.
                retry_after = max(1, int(bucket[0] + self._window - ts) + 1)
                return False, retry_after
            bucket.append(ts)
            return True, 0


def client_ip(request: Request) -> str:
    """Best-effort extraction of the originating client IP.

    Trust order:

    1. ``Fly-Client-IP`` — set by Fly's edge proxy and not forwardable
       by the client. This is the truth on Fly.
    2. ``X-Forwarded-For`` — first hop. Used by other proxies. A
       hostile client behind a non-Fly deployment could spoof this,
       but the rate-limit blast radius is limited to one IP at a
       time, so the worst case is "evades the limit" not "breaks the
       process".
    3. ``request.client.host`` — the socket peer. The right answer
       for a direct connection (local dev). On Fly this would be one
       of the edge IPs, useless for rate limiting.
    """
    fly_ip = request.headers.get("fly-client-ip")
    if fly_ip:
        return fly_ip
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def enforce_generate_limit(request: Request) -> None:
    """FastAPI dependency for the ``/generate`` route.

    Fires before the form body is parsed (the dependency only takes
    ``Request``, so FastAPI does not materialise multipart fields to
    resolve it). A rate-limited client therefore gets a 429 without
    us reading the upload — the bandwidth still costs us a little
    but the expensive Anthropic call is cleanly avoided.
    """
    limiter: RateLimiter = request.app.state.generate_limiter
    allowed, retry_after = limiter.check(client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many memory generations from your address. "
                f"Try again in {retry_after} seconds."
            ),
            headers={"Retry-After": str(retry_after)},
        )
