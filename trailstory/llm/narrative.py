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
from json import JSONDecodeError
from typing import Any

from pydantic import ValidationError

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
) -> NarrativeOutput:
    """Generate a bilingual narrative from hike inputs.

    Args:
        hike_input: Parent's seed text, baby info, and source paths.
        gpx_stats: Parsed GPX stats (distance, elevation, duration, summit).
        photos: Loaded photos. The model selects 6-8 indices into this list,
            so the list must be non-empty.
        client: Anthropic client wrapper. Injected so tests can mock the LLM.
        location: Human-readable place name. Defaults to a generic placeholder;
            callers should supply something specific (e.g. derived from GPX
            or user-provided) when available.

    Returns:
        Validated ``NarrativeOutput``.

    Raises:
        NarrativeGenerationError: photos list empty, LLM errored, the model
            returned non-JSON twice in a row, or the parsed JSON did not
            validate against the schema.
    """
    if not photos:
        raise NarrativeGenerationError("at least one photo is required to build a narrative")

    base_prompt = USER_NARRATIVE_TEMPLATE.format(
        location=location,
        distance_km=gpx_stats.distance_km,
        elevation_gain_m=gpx_stats.elevation_gain_m,
        duration_min=gpx_stats.duration_min,
        summit_elev_m=gpx_stats.summit_elev_m,
        n_photos=len(photos),
        n_photos_minus_1=len(photos) - 1,
        seed_text=hike_input.seed_text,
        baby_name=hike_input.baby_name,
        baby_age_months=hike_input.baby_age_months,
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
        return NarrativeOutput.model_validate(parsed)
    except ValidationError as exc:
        raise NarrativeGenerationError(
            f"LLM JSON did not match NarrativeOutput schema: {exc}"
        ) from exc


# -- internal helpers ---------------------------------------------------------


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
