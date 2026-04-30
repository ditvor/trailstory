"""Anthropic API client wrapper.

This is the only file in the codebase that calls the Anthropic SDK directly and
the only file allowed to call ``.get_secret_value()`` on the API key (see
CLAUDE.md). All higher-level orchestration lives in ``narrative.py``.

The wrapper exposes a single ``complete(prompt, system)`` method and is
deliberately small: retry policy, response validation, and error translation
are all that live here.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from typing import Any, Final

import anthropic
from anthropic import APIError, APIStatusError, RateLimitError
from pydantic import SecretStr

logger = logging.getLogger(__name__)

# Default model — the latest Claude Opus.
#
# Decision recorded in docs/adr/002-narrative-model-choice.md: the narrative is
# the user-facing creative output of the whole tool — a single call per hike,
# read by family members, often translated into Russian where subtle phrasing
# matters. The quality difference materially affects the product and the
# per-hike cost difference is negligible. Callers can still override via the
# constructor or by passing ``settings.model`` from ``config.py``.
DEFAULT_MODEL: Final[str] = "claude-opus-4-7"

DEFAULT_MAX_TOKENS: Final[int] = 4096
DEFAULT_MAX_RETRIES: Final[int] = 3
DEFAULT_BACKOFF_BASE_SECONDS: Final[float] = 1.0
DEFAULT_BACKOFF_MAX_SECONDS: Final[float] = 30.0

# A valid narrative response (JSON with bilingual title, paragraphs, etc.) is
# many hundreds of characters. Anything shorter than this is almost certainly a
# truncated or malformed response and should fail loudly rather than be passed
# downstream.
MIN_RESPONSE_CHARS: Final[int] = 50


class LLMResponseError(Exception):
    """Raised when the model returned an empty or implausibly short response,
    or when the API returned a non-retryable error.

    Stays inside ``llm/`` — ``narrative.py`` is expected to catch this and
    re-raise it as ``NarrativeGenerationError``.
    """


class LLMRetryExhaustedError(Exception):
    """Raised when all rate-limit retry attempts have been exhausted."""


class AnthropicClient:
    """Thin wrapper around :class:`anthropic.Anthropic`.

    Single public method: :meth:`complete`. Retries rate-limit errors with
    exponential backoff and validates that the response is non-empty.
    """

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        """Construct a client.

        Args:
            api_key: Anthropic API key. Unwrapped here, never stored as plain
                text on the instance.
            model: Model identifier. Defaults to :data:`DEFAULT_MODEL`. Pass
                ``settings.model`` from ``config.py`` to honour the env-var
                override.
            max_tokens: Upper bound on response length.
            max_retries: Total attempts (including the first) for rate-limit
                retries. Must be ≥ 1.
            backoff_base_seconds: Base delay for exponential backoff.
            backoff_max_seconds: Cap on a single backoff sleep.
            client: Optional pre-built ``anthropic.Anthropic`` instance. Useful
                in tests so the SDK's network layer can be mocked.
        """
        if max_retries < 1:
            raise ValueError("max_retries must be ≥ 1")

        # Unwrapping the secret here keeps the only ``.get_secret_value()`` call
        # in the codebase contained to this file (see CLAUDE.md).
        self._client = client or anthropic.Anthropic(api_key=api_key.get_secret_value())
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds

    @property
    def model(self) -> str:
        """The model identifier this client was configured with."""
        return self._model

    def complete(self, prompt: str, system: str) -> str:
        """Send a single user message and return the text response.

        Retries rate-limit errors with exponential backoff up to
        ``max_retries`` total attempts. Validates the response is non-empty
        and at least :data:`MIN_RESPONSE_CHARS` long.

        Args:
            prompt: User-role message content.
            system: System prompt.

        Returns:
            The concatenated text from the assistant's response, stripped of
            surrounding whitespace.

        Raises:
            LLMRetryExhaustedError: All rate-limit retries were used up.
            LLMResponseError: Empty / too-short response, or a non-retryable
                API error.
        """
        last_rate_limit: RateLimitError | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                message = self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
            except RateLimitError as exc:
                last_rate_limit = exc
                if attempt >= self._max_retries:
                    break
                delay = self._compute_backoff(attempt)
                logger.warning(
                    "anthropic rate-limited (attempt %d/%d); sleeping %.2fs",
                    attempt,
                    self._max_retries,
                    delay,
                )
                time.sleep(delay)
                continue
            except APIStatusError as exc:
                # Non-rate-limit status errors (4xx other than 429, or 5xx).
                # Translate to LLMResponseError so callers don't see SDK types.
                raise LLMResponseError(f"Anthropic API error {exc.status_code}: {exc}") from exc
            except APIError as exc:
                # Connection errors, timeouts, schema errors, etc.
                raise LLMResponseError(f"Anthropic API error: {exc}") from exc

            text = self._extract_text(message)
            self._validate_text(text)
            return text

        raise LLMRetryExhaustedError(
            f"Rate-limit retries exhausted after {self._max_retries} attempts"
        ) from last_rate_limit

    def complete_stream(self, prompt: str, system: str) -> Iterator[str]:
        """Stream the assistant response as text chunks.

        Mirrors :meth:`complete` but yields each text delta as it arrives so
        callers (the FastAPI SSE endpoint) can push tokens to the browser
        while the model is still writing. Rate-limit errors that fire
        *before* any chunk is yielded are retried with the same exponential
        backoff as :meth:`complete`; any error that fires *after* yielding
        begins is surfaced as :class:`LLMResponseError` (re-yielding chunks
        the caller has already consumed would corrupt the SSE stream, so we
        don't try).

        Args:
            prompt: User-role message content.
            system: System prompt.

        Yields:
            Each text delta in arrival order. The full response is the
            concatenation of the yielded chunks.

        Raises:
            LLMRetryExhaustedError: All rate-limit retries were used up
                before a single chunk was emitted.
            LLMResponseError: Empty stream, mid-stream error, or
                non-retryable API error.
        """
        last_rate_limit: RateLimitError | None = None

        for attempt in range(1, self._max_retries + 1):
            chunks_emitted = False
            try:
                with self._client.messages.stream(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                ) as stream:
                    for chunk in stream.text_stream:
                        if chunk:
                            chunks_emitted = True
                            yield chunk
                if not chunks_emitted:
                    raise LLMResponseError("Anthropic API returned an empty stream.")
                return
            except RateLimitError as exc:
                if chunks_emitted:
                    # Cannot retry once chunks are out the door.
                    raise LLMResponseError(f"Anthropic API rate-limited mid-stream: {exc}") from exc
                last_rate_limit = exc
                if attempt >= self._max_retries:
                    break
                delay = self._compute_backoff(attempt)
                logger.warning(
                    "anthropic stream rate-limited (attempt %d/%d); sleeping %.2fs",
                    attempt,
                    self._max_retries,
                    delay,
                )
                time.sleep(delay)
                continue
            except APIStatusError as exc:
                raise LLMResponseError(f"Anthropic API error {exc.status_code}: {exc}") from exc
            except APIError as exc:
                raise LLMResponseError(f"Anthropic API error: {exc}") from exc

        raise LLMRetryExhaustedError(
            f"Rate-limit retries exhausted after {self._max_retries} attempts"
        ) from last_rate_limit

    # -- internal helpers ----------------------------------------------------

    def _compute_backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter, capped at ``backoff_max_seconds``."""
        ceiling = min(
            self._backoff_base_seconds * (2 ** (attempt - 1)),
            self._backoff_max_seconds,
        )
        # Full jitter: pick uniformly in [0, ceiling]. Smooths thundering-herd
        # behaviour on shared rate limits.
        return random.uniform(0.0, ceiling)

    @staticmethod
    def _extract_text(message: Any) -> str:
        """Concatenate the text from all text blocks in the response.

        The Anthropic SDK returns a list of content blocks; for our prompts we
        expect a single text block, but we concatenate defensively.
        """
        chunks: list[str] = []
        for block in getattr(message, "content", None) or []:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                chunks.append(text)
        return "".join(chunks).strip()

    @staticmethod
    def _validate_text(text: str) -> None:
        if not text:
            raise LLMResponseError("Anthropic API returned an empty response.")
        if len(text) < MIN_RESPONSE_CHARS:
            raise LLMResponseError(
                f"Anthropic API response too short "
                f"({len(text)} chars, expected ≥ {MIN_RESPONSE_CHARS})."
            )
