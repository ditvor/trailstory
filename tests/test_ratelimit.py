"""Tests for the per-IP rate limiter on ``/generate``.

The limiter unit tests use injected ``now`` timestamps so we exercise
window expiry without ``time.sleep`` (deterministic, sub-millisecond).
The ``client_ip`` tests use a stubbed Starlette ``Request`` rather
than the FastAPI ``TestClient`` so we can assert each header path in
isolation. The route-level 429 path is exercised in ``test_web.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from web.ratelimit import RateLimiter, client_ip


def _request(headers: dict[str, str], peer: str | None = "127.0.0.1") -> Any:
    """Build a minimal stand-in for :class:`fastapi.Request`.

    ``client_ip`` only reads ``request.headers`` and ``request.client``,
    so a MagicMock is enough — we avoid spinning up a real Starlette
    Request just for header lookup.
    """
    request = MagicMock()
    request.headers = headers
    if peer is None:
        request.client = None
    else:
        client = MagicMock()
        client.host = peer
        request.client = client
    return request


# ── RateLimiter ──────────────────────────────────────────────────────────────


def test_limiter_allows_up_to_limit() -> None:
    rl = RateLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        allowed, retry = rl.check("ip1", now=100.0)
        assert allowed is True
        assert retry == 0


def test_limiter_rejects_over_limit_with_retry_after() -> None:
    rl = RateLimiter(limit=2, window_seconds=60)
    rl.check("ip1", now=100.0)
    rl.check("ip1", now=110.0)
    allowed, retry = rl.check("ip1", now=120.0)
    assert allowed is False
    # Oldest entry was at 100; window 60s → ages out at 160; +1 buffer.
    assert retry == 41


def test_limiter_separates_keys() -> None:
    rl = RateLimiter(limit=1, window_seconds=60)
    assert rl.check("a", now=100.0) == (True, 0)
    assert rl.check("b", now=100.0) == (True, 0)
    assert rl.check("a", now=100.0)[0] is False
    assert rl.check("b", now=100.0)[0] is False


def test_limiter_window_expiry_restores_quota() -> None:
    rl = RateLimiter(limit=1, window_seconds=10)
    assert rl.check("ip", now=100.0)[0] is True
    assert rl.check("ip", now=105.0)[0] is False
    # 11s past the original entry — it has aged out of the 10s window.
    assert rl.check("ip", now=111.0)[0] is True


def test_limiter_uses_real_clock_when_now_is_none() -> None:
    """A bare ``check(key)`` call should still work — we just want
    a smoke test that the wall-clock branch exercises without
    raising. Two calls back-to-back are within the default window
    so both are allowed under a generous limit."""
    rl = RateLimiter(limit=10, window_seconds=60)
    assert rl.check("ip")[0] is True
    assert rl.check("ip")[0] is True


def test_limiter_evicts_oldest_when_max_keys_hit() -> None:
    """A flood of unique IPs must not grow the bucket map without bound.

    With ``max_keys=2``, inserting a third key evicts the first.
    We can't see the dict directly, but the eviction behaviour is
    observable: the evicted key gets a fresh bucket on its next
    request even after it was previously over-quota.
    """
    rl = RateLimiter(limit=1, window_seconds=60, max_keys=2)
    rl.check("a", now=1.0)  # bucket for "a" full
    rl.check("b", now=2.0)  # bucket for "b" full
    rl.check("c", now=3.0)  # forces eviction of "a"
    # If "a" had been preserved, this would be denied.
    assert rl.check("a", now=4.0)[0] is True


def test_limiter_rejects_zero_or_negative_limit() -> None:
    with pytest.raises(ValueError):
        RateLimiter(limit=0, window_seconds=60)
    with pytest.raises(ValueError):
        RateLimiter(limit=-1, window_seconds=60)
    with pytest.raises(ValueError):
        RateLimiter(limit=1, window_seconds=0)


# ── client_ip ────────────────────────────────────────────────────────────────


def test_client_ip_prefers_fly_client_ip_header() -> None:
    request = _request(
        headers={
            "fly-client-ip": "203.0.113.1",
            "x-forwarded-for": "198.51.100.2",
        },
        peer="10.0.0.1",
    )
    assert client_ip(request) == "203.0.113.1"


def test_client_ip_falls_back_to_xff_first_hop() -> None:
    request = _request(
        headers={"x-forwarded-for": "203.0.113.1, 198.51.100.2, 10.0.0.1"},
        peer="10.0.0.1",
    )
    assert client_ip(request) == "203.0.113.1"


def test_client_ip_falls_back_to_socket_peer() -> None:
    request = _request(headers={}, peer="127.0.0.1")
    assert client_ip(request) == "127.0.0.1"


def test_client_ip_returns_unknown_when_no_signal() -> None:
    request = _request(headers={}, peer=None)
    assert client_ip(request) == "unknown"
