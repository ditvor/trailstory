"""LLM narrative orchestrator.

Owns the request / response lifecycle for narrative generation: format the
user prompt from hike data, call the Anthropic client, parse and validate
the JSON, and return a ``NarrativeOutput``.

Per CLAUDE.md, this module:

* never builds prompt strings inline — text comes from
  ``trailstory.llm.prompts``;
* never calls the Anthropic SDK directly — it goes through
  ``trailstory.llm.client.AnthropicClient``;
* funnels every failure mode (LLM error, parse failure, schema-validation
  failure) into ``NarrativeGenerationError``, the only narrative-side
  exception type that is allowed to escape ``llm/``.

Retry policy:

* The client itself retries rate-limit errors (3 attempts).
* This orchestrator retries **once more** when the response is well-
  formed text but does not parse as JSON, with an explicit
  ``USER_NARRATIVE_RETRY_SUFFIX`` directive appended to the user prompt.
* Schema-validation failures (JSON parses but does not match
  ``NarrativeOutput``) are **not** retried — they indicate a model-side
  shape problem and we want to surface them immediately rather than burn
  another paid call.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

from pydantic import ValidationError

from trailstory.llm import cache as narrative_cache
from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.llm.prompts import (
    SYSTEM_NARRATIVE,
    USER_NARRATIVE_RETRY_SUFFIX,
    USER_NARRATIVE_TEMPLATE,
)
from trailstory.models import GpxStats, HikeInput, NarrativeOutput, PhotoMeta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NarrativeStreamChunk:
    """A text delta yielded by the streaming narrative pipeline.

    The SSE endpoint forwards each chunk to the browser as an event so the
    user sees the model writing in real time.
    """

    text: str


@dataclass(frozen=True)
class NarrativeStreamRetry:
    """The first attempt produced unparseable output; we are retrying once.

    Emitted between the failed attempt and the second one so the SSE
    consumer can flip to a "regenerating" UI state.
    """

    reason: str


@dataclass(frozen=True)
class NarrativeStreamComplete:
    """Final event: the streamed output validated cleanly into a narrative."""

    narrative: NarrativeOutput


NarrativeStreamEvent = NarrativeStreamChunk | NarrativeStreamRetry | NarrativeStreamComplete


class NarrativeGenerationError(Exception):
    """Terminal failure of the narrative pipeline.

    Raised when the LLM call ultimately fails, when both JSON-parse
    attempts return non-JSON, or when the parsed JSON does not validate
    against ``NarrativeOutput``. Higher layers (CLI) catch this and
    surface it to the user.
    """


def generate_narrative(
    hike_input: HikeInput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    *,
    client: AnthropicClient,
    location: str = "the trail",
    use_cache: bool = True,
) -> NarrativeOutput:
    """Generate a tri-lingual narrative from hike inputs.

    Args:
        hike_input: Hiker's seed text and source paths.
        gpx_stats: Parsed GPX stats (distance, elevation, duration, summit).
        photos: Loaded photos. The model selects 6-8 indices into this list,
            so the list must be non-empty.
        client: Anthropic client wrapper. Injected so tests can mock the LLM.
        location: Fallback place name. ``hike_input.location_name`` wins when
            set; this kwarg is the default the prompt sees otherwise.
        use_cache: When ``True`` (the default), check the on-disk cache
            first and write any newly generated narrative back to it.
            Set to ``False`` for tests that need to assert call counts on
            the mocked client, and from the CLI's ``--no-cache`` flag.

    Returns:
        Validated ``NarrativeOutput``.

    Raises:
        NarrativeGenerationError: photos list empty, LLM errored, the model
            returned non-JSON twice in a row, or the parsed JSON did not
            validate against the schema.
    """
    if not photos:
        raise NarrativeGenerationError("at least one photo is required to build a narrative")

    key: str | None = None
    if use_cache:
        key = narrative_cache.cache_key(hike_input, gpx_stats, photos, client.model)
        cached = narrative_cache.get(key)
        if cached is not None:
            logger.info("narrative cache hit for key %s", key)
            return cached
        logger.info("narrative cache miss for key %s", key)

    place = hike_input.location_name or location
    base_prompt = USER_NARRATIVE_TEMPLATE.format(
        location=place,
        distance_km=gpx_stats.distance_km,
        elevation_gain_m=gpx_stats.elevation_gain_m,
        duration_min=gpx_stats.duration_min,
        summit_elev_m=gpx_stats.summit_elev_m,
        n_photos=len(photos),
        n_photos_minus_1=len(photos) - 1,
        seed_text=hike_input.seed_text,
    )

    parsed = _call_and_parse(client, base_prompt)
    if parsed is None:
        # Step 7 spec: retry exactly once with an explicit JSON-only directive.
        logger.warning("first response did not parse as JSON; retrying with explicit directive")
        retry_prompt = base_prompt + USER_NARRATIVE_RETRY_SUFFIX
        parsed = _call_and_parse(client, retry_prompt)
        if parsed is None:
            raise NarrativeGenerationError("Model returned non-JSON output on both attempts.")

    try:
        narrative = NarrativeOutput.model_validate(parsed)
    except ValidationError as exc:
        raise NarrativeGenerationError(
            f"LLM JSON did not match NarrativeOutput schema: {exc}"
        ) from exc

    if key is not None:
        narrative_cache.put(key, narrative)
    return narrative


def generate_narrative_stream(
    hike_input: HikeInput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    *,
    client: AnthropicClient,
    location: str = "the trail",
) -> Iterator[NarrativeStreamEvent]:
    """Streaming variant of :func:`generate_narrative`.

    Yields a :class:`NarrativeStreamChunk` for every text delta from the
    LLM, optionally a :class:`NarrativeStreamRetry` between attempts when
    the first response did not parse, and finally a
    :class:`NarrativeStreamComplete` with the validated
    :class:`NarrativeOutput`.

    The retry policy mirrors :func:`generate_narrative`: one extra attempt
    with :data:`USER_NARRATIVE_RETRY_SUFFIX` when the first response was
    unparseable. Schema-validation failures are not retried — they raise
    :class:`NarrativeGenerationError` immediately, the same as the
    non-streaming pipeline.

    The cache is intentionally bypassed for streaming runs: the user is
    looking at a "writing your story" page and expects to see the words
    appear, so a cached instant return would be jarring. The
    non-streaming :func:`generate_narrative` keeps the cache for CLI use.

    Args:
        hike_input: Hiker's seed text and source paths.
        gpx_stats: Parsed GPX stats.
        photos: Loaded photos. The model selects 6-8 indices into this list.
        client: Anthropic client wrapper. Must implement ``complete_stream``.
        location: Fallback place name when ``hike_input.location_name`` is
            unset.

    Yields:
        :class:`NarrativeStreamEvent` instances. The terminal event is
        always :class:`NarrativeStreamComplete` on success.

    Raises:
        NarrativeGenerationError: photos list empty, LLM call failed, the
            model returned non-JSON twice in a row, or the parsed JSON did
            not validate against the schema.
    """
    if not photos:
        raise NarrativeGenerationError("at least one photo is required to build a narrative")

    place = hike_input.location_name or location
    base_prompt = USER_NARRATIVE_TEMPLATE.format(
        location=place,
        distance_km=gpx_stats.distance_km,
        elevation_gain_m=gpx_stats.elevation_gain_m,
        duration_min=gpx_stats.duration_min,
        summit_elev_m=gpx_stats.summit_elev_m,
        n_photos=len(photos),
        n_photos_minus_1=len(photos) - 1,
        seed_text=hike_input.seed_text,
    )

    parsed, chunks_first = _stream_and_parse(client, base_prompt)
    yield from (NarrativeStreamChunk(text=c) for c in chunks_first)

    if parsed is None:
        logger.warning(
            "first streamed response did not parse as JSON; retrying with explicit directive"
        )
        yield NarrativeStreamRetry(reason="model output was not valid JSON; retrying once")
        retry_prompt = base_prompt + USER_NARRATIVE_RETRY_SUFFIX
        parsed, chunks_retry = _stream_and_parse(client, retry_prompt)
        yield from (NarrativeStreamChunk(text=c) for c in chunks_retry)
        if parsed is None:
            raise NarrativeGenerationError("Model returned non-JSON output on both attempts.")

    try:
        narrative = NarrativeOutput.model_validate(parsed)
    except ValidationError as exc:
        raise NarrativeGenerationError(
            f"LLM JSON did not match NarrativeOutput schema: {exc}"
        ) from exc

    yield NarrativeStreamComplete(narrative=narrative)


# -- internal helpers ---------------------------------------------------------


def _stream_and_parse(
    client: AnthropicClient, prompt: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """Stream a single attempt, accumulate text, try to parse as JSON.

    Returns ``(parsed_dict_or_None, chunks)`` so the caller can yield
    each chunk to the SSE consumer in arrival order.
    """
    chunks: list[str] = []
    try:
        for chunk in client.complete_stream(prompt=prompt, system=SYSTEM_NARRATIVE):
            chunks.append(chunk)
    except (LLMResponseError, LLMRetryExhaustedError) as exc:
        raise NarrativeGenerationError(f"LLM call failed: {exc}") from exc

    cleaned = _strip_code_fences("".join(chunks))
    try:
        result = json.loads(cleaned)
    except JSONDecodeError:
        return None, chunks
    return (result if isinstance(result, dict) else None), chunks


def _call_and_parse(client: AnthropicClient, prompt: str) -> dict[str, Any] | None:
    """Call the LLM once and try to parse the response as a JSON object.

    Returns the parsed ``dict`` on success, or ``None`` if the response is
    not a JSON object (parse failure, JSON array, JSON literal, etc.) so
    the caller can decide whether to retry. Client-level errors are
    funnelled into ``NarrativeGenerationError`` immediately because
    retrying them would duplicate the client's own retry policy.
    """
    try:
        raw = client.complete(prompt=prompt, system=SYSTEM_NARRATIVE)
    except (LLMResponseError, LLMRetryExhaustedError) as exc:
        raise NarrativeGenerationError(f"LLM call failed: {exc}") from exc

    cleaned = _strip_code_fences(raw)
    try:
        result = json.loads(cleaned)
    except JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _strip_code_fences(text: str) -> str:
    """Remove a leading / trailing markdown code fence if present.

    Tolerates the common case where the model wraps its JSON in
    `````json`` ... ``````` despite being
    asked not to. Pure prose responses pass through unchanged and are
    caught downstream by ``json.loads``.
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    # Drop the opening fence (``` or ```json) ...
    if lines[0].startswith("```"):
        lines = lines[1:]
    # ... and the closing fence if present.
    if lines and lines[-1].rstrip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
