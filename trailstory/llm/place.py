"""Place-context stitch call (ADR-017).

The one LLM call allowed to consume external knowledge — and only because
that knowledge arrives as a supplied, citable :class:`PlaceReference`, never
from the model's own memory. It takes the reference extract plus the hiker's
own ledger-grounded place beats and produces a tri-lingual
:class:`PlaceContext` summary that connects them.

Per CLAUDE.md this module owns no prompt strings (they live in
``trailstory.llm.prompts``) and never calls the SDK directly (it goes through
``trailstory.llm.client.AnthropicClient``).

Unlike the narrative pipeline, every failure here **soft-fails to ``None``**:
the place block is additive, and a failed geocode, a model error, or
unparseable JSON must omit the block, not break the render.
"""

from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.llm.prompts import (
    SYSTEM_PLACE_CONTEXT,
    USER_PLACE_CONTEXT_RETRY_SUFFIX,
    USER_PLACE_CONTEXT_TEMPLATE,
)
from trailstory.models import FactLedger, LocalizedString, PlaceContext
from trailstory.place import PlaceReference

logger = logging.getLogger(__name__)


class _PlaceOutput(BaseModel):
    """Shape the stitch model returns; mapped onto :class:`PlaceContext`.

    ``extra="ignore"`` so a stray key the model adds doesn't fail validation
    — this is a soft-fail path and we want the summary if it's well-formed.
    """

    model_config = ConfigDict(extra="ignore")

    summary: LocalizedString
    used_hiker_details: list[str] = Field(default_factory=list)


def place_beats_from_ledger(ledger: FactLedger) -> list[str]:
    """Collect the hiker's own place-related detail strings from the ledger.

    The concrete nouns the hiker named (``chronology[*].objects_mentioned``)
    plus their verbatim phrases — all already ledger-grounded, so handing
    them to the stitch introduces no new facts. De-duplicated, order
    preserved (chronological, then phrases).
    """
    beats: list[str] = []
    for beat in ledger.chronology:
        for obj in beat.objects_mentioned:
            if obj not in beats:
                beats.append(obj)
    for phrase in ledger.verbatim_user_phrases:
        if phrase not in beats:
            beats.append(phrase)
    return beats


def generate_place_context(
    reference: PlaceReference,
    place_beats: list[str],
    *,
    client: AnthropicClient,
) -> PlaceContext | None:
    """Stitch a tri-lingual place note from a reference + the hiker's beats.

    Args:
        reference: Grounded external facts (town, region, Wikipedia extract,
            attribution) from :func:`trailstory.place.resolve_place_reference`.
        place_beats: The hiker's own place-related detail strings, from
            :func:`place_beats_from_ledger`. May be empty.
        client: Anthropic client wrapper, typically built with
            ``Settings.place_model``. Injected so tests can mock.

    Returns:
        A validated :class:`PlaceContext`, or ``None`` if the model errored,
        returned non-JSON on both attempts, or produced JSON that did not
        validate. Callers treat ``None`` as "omit the block".
    """
    user_prompt = USER_PLACE_CONTEXT_TEMPLATE.format(
        town=reference.town,
        region=reference.region or "unknown",
        source_extract=reference.extract or "",
        hiker_place_beats_json=json.dumps(place_beats, ensure_ascii=False),
    )

    parsed = _call_and_parse(client, user_prompt)
    if parsed is None:
        logger.info("place-context response did not parse; retrying once with JSON-only directive")
        parsed = _call_and_parse(client, user_prompt + USER_PLACE_CONTEXT_RETRY_SUFFIX)
    if parsed is None:
        logger.warning("place-context returned non-JSON on both attempts; omitting block")
        return None

    try:
        out = _PlaceOutput.model_validate(parsed)
    except ValidationError as exc:
        logger.warning("place-context JSON did not validate (%s); omitting block", exc)
        return None

    return PlaceContext(
        town=reference.town,
        region=reference.region,
        summary=out.summary,
        used_hiker_details=out.used_hiker_details,
        source_url=reference.source_url,
        source_title=reference.source_title,
    )


# -- internal helpers ---------------------------------------------------------


def _call_and_parse(client: AnthropicClient, prompt: str) -> dict[str, Any] | None:
    """Call the stitch model once and parse the response as a JSON object.

    Returns the parsed ``dict`` on success, or ``None`` on a client-level
    error (the place block is optional — a failed call omits it rather than
    raising) or a non-object response, so the caller can decide whether to
    retry.
    """
    try:
        raw = client.complete(prompt=prompt, system=SYSTEM_PLACE_CONTEXT)
    except (LLMResponseError, LLMRetryExhaustedError) as exc:
        logger.warning("place-context LLM call failed: %s", exc)
        return None

    cleaned = _strip_code_fences(raw)
    try:
        result = json.loads(cleaned)
    except JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _strip_code_fences(text: str) -> str:
    """Remove a leading / trailing markdown code fence if present.

    Mirrors ``trailstory.llm.narrative._strip_code_fences``: tolerate the
    model wrapping JSON in a ``` fence despite being asked not to.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
