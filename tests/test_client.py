"""Tests for ``trailstory.llm.client``.

These tests exercise the client in isolation: every test injects a mock
``anthropic.Anthropic`` instance via the constructor's ``client=`` parameter,
so no network calls are made (per CLAUDE.md: "Never call the real Anthropic
API in tests").

We also patch ``time.sleep`` inside the client module so retry tests run
instantly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from anthropic import APIConnectionError, APIStatusError, RateLimitError
from pydantic import SecretStr

from trailstory.llm.client import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    MIN_RESPONSE_CHARS,
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)

API_KEY = SecretStr("sk-test-fake")


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_message(text: str | list[str]) -> Any:
    """Build a fake ``messages.create`` return value.

    The SDK returns an object with a ``.content`` list of blocks, each having
    a ``.text`` attribute. We mimic that shape with simple namespaces.
    """
    chunks = [text] if isinstance(text, str) else text
    blocks = [MagicMock(text=chunk) for chunk in chunks]
    return MagicMock(content=blocks)


def _make_rate_limit_error(msg: str = "rate limited") -> RateLimitError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)
    return RateLimitError(msg, response=response, body=None)


def _make_status_error(status: int, msg: str = "server error") -> APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return APIStatusError(msg, response=response, body=None)


def _build_client(
    *,
    sdk_mock: MagicMock | None = None,
    **kwargs: Any,
) -> tuple[AnthropicClient, MagicMock]:
    """Construct an ``AnthropicClient`` wired to a mock SDK instance."""
    sdk = sdk_mock or MagicMock()
    client = AnthropicClient(API_KEY, client=sdk, **kwargs)
    return client, sdk


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``time.sleep`` inside the client so backoff doesn't slow tests."""
    monkeypatch.setattr("trailstory.llm.client.time.sleep", lambda _s: None)


# ── happy path ────────────────────────────────────────────────────────────────


def test_complete_returns_text_on_success() -> None:
    body = "x" * (MIN_RESPONSE_CHARS + 10)
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message(body)
    client, _ = _build_client(sdk_mock=sdk)

    out = client.complete(prompt="hello", system="be helpful")

    assert out == body
    sdk.messages.create.assert_called_once()
    kwargs = sdk.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["system"] == "be helpful"
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]


def test_complete_strips_surrounding_whitespace() -> None:
    body = "   " + ("y" * (MIN_RESPONSE_CHARS + 5)) + "\n\n"
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message(body)
    client, _ = _build_client(sdk_mock=sdk)

    out = client.complete("p", "s")

    assert out == body.strip()


def test_complete_concatenates_multiple_text_blocks() -> None:
    parts = ["alpha " * 10, "beta " * 10]
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message(parts)
    client, _ = _build_client(sdk_mock=sdk)

    out = client.complete("p", "s")

    assert out == ("".join(parts)).strip()


def test_complete_uses_configured_model() -> None:
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message("z" * 200)
    client, _ = _build_client(sdk_mock=sdk, model="claude-sonnet-4-6")

    client.complete("p", "s")

    assert sdk.messages.create.call_args.kwargs["model"] == "claude-sonnet-4-6"
    assert client.model == "claude-sonnet-4-6"


# ── response validation ──────────────────────────────────────────────────────


def test_complete_raises_on_empty_response() -> None:
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message("")
    client, _ = _build_client(sdk_mock=sdk)

    with pytest.raises(LLMResponseError, match="empty"):
        client.complete("p", "s")


def test_complete_raises_on_whitespace_only_response() -> None:
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message("   \n\t ")
    client, _ = _build_client(sdk_mock=sdk)

    with pytest.raises(LLMResponseError, match="empty"):
        client.complete("p", "s")


def test_complete_raises_on_short_response() -> None:
    short = "x" * (MIN_RESPONSE_CHARS - 1)
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message(short)
    client, _ = _build_client(sdk_mock=sdk)

    with pytest.raises(LLMResponseError, match="too short"):
        client.complete("p", "s")


def test_complete_accepts_response_at_threshold() -> None:
    body = "x" * MIN_RESPONSE_CHARS
    sdk = MagicMock()
    sdk.messages.create.return_value = _make_message(body)
    client, _ = _build_client(sdk_mock=sdk)

    assert client.complete("p", "s") == body


# ── retry behaviour ──────────────────────────────────────────────────────────


def test_complete_retries_rate_limit_then_succeeds() -> None:
    body = "ok " * 30
    sdk = MagicMock()
    sdk.messages.create.side_effect = [
        _make_rate_limit_error(),
        _make_rate_limit_error(),
        _make_message(body),
    ]
    client, _ = _build_client(sdk_mock=sdk)

    out = client.complete("p", "s")

    assert out == body.strip()
    assert sdk.messages.create.call_count == 3


def test_complete_raises_retry_exhausted_after_max_attempts() -> None:
    sdk = MagicMock()
    sdk.messages.create.side_effect = _make_rate_limit_error()
    client, _ = _build_client(sdk_mock=sdk)

    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        client.complete("p", "s")

    # Exactly DEFAULT_MAX_RETRIES attempts — no more, no less.
    assert sdk.messages.create.call_count == DEFAULT_MAX_RETRIES
    # The last RateLimitError chains as __cause__.
    assert isinstance(exc_info.value.__cause__, RateLimitError)


def test_complete_respects_custom_max_retries() -> None:
    sdk = MagicMock()
    sdk.messages.create.side_effect = _make_rate_limit_error()
    client, _ = _build_client(sdk_mock=sdk, max_retries=5)

    with pytest.raises(LLMRetryExhaustedError):
        client.complete("p", "s")
    assert sdk.messages.create.call_count == 5


def test_complete_uses_exponential_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff ceiling grows as 2^(n-1) * base, capped at backoff_max."""
    sleeps: list[float] = []
    monkeypatch.setattr("trailstory.llm.client.time.sleep", lambda s: sleeps.append(s))
    # Make jitter deterministic: pick the ceiling.
    monkeypatch.setattr("trailstory.llm.client.random.uniform", lambda _lo, hi: hi)

    sdk = MagicMock()
    sdk.messages.create.side_effect = _make_rate_limit_error()
    client, _ = _build_client(
        sdk_mock=sdk,
        max_retries=4,
        backoff_base_seconds=1.0,
        backoff_max_seconds=10.0,
    )

    with pytest.raises(LLMRetryExhaustedError):
        client.complete("p", "s")

    # 4 attempts → 3 sleeps. Ceilings: 1, 2, 4.
    assert sleeps == [1.0, 2.0, 4.0]


def test_complete_caps_backoff_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("trailstory.llm.client.time.sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr("trailstory.llm.client.random.uniform", lambda _lo, hi: hi)

    sdk = MagicMock()
    sdk.messages.create.side_effect = _make_rate_limit_error()
    client, _ = _build_client(
        sdk_mock=sdk,
        max_retries=6,
        backoff_base_seconds=1.0,
        backoff_max_seconds=3.0,  # cap below 2^(n-1) growth
    )
    with pytest.raises(LLMRetryExhaustedError):
        client.complete("p", "s")

    # Ceilings would be 1, 2, 4, 8, 16 — capped at 3 from the third sleep on.
    assert sleeps == [1.0, 2.0, 3.0, 3.0, 3.0]


# ── non-retryable errors ─────────────────────────────────────────────────────


def test_complete_translates_status_error() -> None:
    sdk = MagicMock()
    sdk.messages.create.side_effect = _make_status_error(500, "boom")
    client, _ = _build_client(sdk_mock=sdk)

    with pytest.raises(LLMResponseError, match="500"):
        client.complete("p", "s")
    # No retry on non-rate-limit status errors.
    assert sdk.messages.create.call_count == 1


def test_complete_translates_connection_error() -> None:
    sdk = MagicMock()
    sdk.messages.create.side_effect = APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    client, _ = _build_client(sdk_mock=sdk)

    with pytest.raises(LLMResponseError):
        client.complete("p", "s")
    assert sdk.messages.create.call_count == 1


# ── constructor ──────────────────────────────────────────────────────────────


def test_constructor_rejects_zero_max_retries() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        AnthropicClient(API_KEY, max_retries=0, client=MagicMock())


def test_constructor_default_model_is_opus() -> None:
    """Documented default: opus, justified in the client module comment."""
    client, _ = _build_client()
    assert client.model == DEFAULT_MODEL
    assert DEFAULT_MODEL == "claude-opus-4-6"


def test_constructor_does_not_store_plaintext_secret() -> None:
    """Defence-in-depth: the SecretStr should not be on the instance dict in plaintext."""
    client, _ = _build_client()
    # The unwrapped key must not appear anywhere on the instance.
    assert not any(API_KEY.get_secret_value() == v for v in vars(client).values())
