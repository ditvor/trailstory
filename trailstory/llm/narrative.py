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
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trailstory.daylight import daylight_context as _daylight_context
from trailstory.gpx import correlate_photos_to_track
from trailstory.llm import cache as narrative_cache
from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.llm.prompts import (
    SYSTEM_LEDGER_EXTRACTOR,
    SYSTEM_NARRATIVE,
    USER_LEDGER_EXTRACTOR_TEMPLATE,
    USER_LEDGER_RETRY_SUFFIX,
    USER_NARRATIVE_RETRY_SUFFIX,
    USER_NARRATIVE_TEMPLATE,
    VERIFIER_VERBATIM_FEEDBACK_TEMPLATE,
)
from trailstory.models import (
    Beat,
    FactLedger,
    GpxStats,
    HikeInput,
    NarrativeOutput,
    Person,
    PhotoMeta,
    ProvenanceSource,
    Waypoint,
)

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


class LedgerExtractionError(Exception):
    """Terminal failure of the ledger-extraction pass (ADR-009, Phase 2).

    Same retry policy and failure-mode separation as
    :class:`NarrativeGenerationError`: client-level errors propagate up
    immediately, JSON-parse failures retry once, schema-validation
    failures surface without retry. Higher layers (the narrative
    orchestrator) catch this and re-raise as
    :class:`NarrativeGenerationError` so callers above the LLM layer
    only have to handle one error type.
    """


class _ExtractorOutput(BaseModel):
    """LLM-derived subset of :class:`FactLedger`.

    The extractor returns only the fields it can reason about (people
    named in the seed, the weather phrase, the chronology beats).
    Deterministic fields (GPX stats, derived season, photo count) are
    merged in Python by :func:`extract_ledger` — feeding numbers through
    an LLM just invites transcription errors. Keeping the LLM contract
    narrow also lets us validate a small, predictable JSON shape.
    """

    model_config = ConfigDict(frozen=True)

    people: list[Person]
    weather: str
    chronology: list[Beat]
    # ADR-016: short literal quotes from the seed text. Default keeps
    # older-shaped extractor responses (and test mocks) validating; the
    # field is re-verified as genuine seed substrings in Python by
    # :func:`_filter_verbatim_phrases` before it reaches the ledger.
    verbatim_user_phrases: list[str] = Field(default_factory=list)


# ADR-016 bounds for the verbatim-phrase anchor. The extractor is asked
# for 2-4 phrases of ≤ 8 words; the filter below enforces both so a
# chatty extractor can't flood the writer prompt with quotes.
_VERBATIM_PHRASE_MAX_WORDS = 8
_VERBATIM_PHRASE_MAX_COUNT = 4


def _filter_verbatim_phrases(phrases: list[str], seed_text: str) -> list[str]:
    """Keep only phrases that really are verbatim quotes of the seed.

    The extractor prompt asks for character-for-character copies, but an
    LLM's "verbatim" cannot be trusted — this filter makes the guarantee
    deterministic. A phrase survives only if it is a case-insensitive
    literal substring of the seed text and at most
    ``_VERBATIM_PHRASE_MAX_WORDS`` words long; duplicates collapse and at
    most ``_VERBATIM_PHRASE_MAX_COUNT`` survive, in extractor order. An
    empty result is a valid outcome (thin seed, paraphrasing extractor) —
    the writer prompt and the verifier both treat it as "proceed without".
    """
    haystack = seed_text.casefold()
    seen: set[str] = set()
    kept: list[str] = []
    for phrase in phrases:
        candidate = phrase.strip()
        if not candidate or len(candidate.split()) > _VERBATIM_PHRASE_MAX_WORDS:
            continue
        key = candidate.casefold()
        if key in seen or key not in haystack:
            continue
        seen.add(key)
        kept.append(candidate)
        if len(kept) == _VERBATIM_PHRASE_MAX_COUNT:
            break
    return kept


# Northern-hemisphere meteorological season per calendar month. December
# rolls forward into winter so the lookup is by month number 1-12 without
# a special case at year boundary. Southern hemisphere inverts this by
# shifting six months — done explicitly in :func:`_infer_date_and_season`.
_NORTHERN_SEASON_BY_MONTH: dict[int, str] = {
    12: "winter",
    1: "winter",
    2: "winter",
    3: "spring",
    4: "spring",
    5: "spring",
    6: "summer",
    7: "summer",
    8: "summer",
    9: "autumn",
    10: "autumn",
    11: "autumn",
}


def _infer_date_and_season(gpx_stats: GpxStats) -> tuple[str, str]:
    """Derive (date_iso, season_phrase) from the first timed waypoint.

    Used to ground the writer prompt under ADR-008 — without temporal
    context the writer would describe an April hike in "summer-milky"
    river terms (an observed failure mode in pilot output).

    Returns ``("unknown", "unknown")`` when no waypoint carries a
    timestamp (some manually-edited GPX files strip timing). Hemisphere
    is inferred from the waypoint's latitude; southern-hemisphere months
    map through the 6-month shift so e.g. April → autumn there.

    The season phrase is intentionally verbose ("spring (April; northern
    hemisphere)") so the model has both the high-level label and the
    specific month + hemisphere in one string. The redundancy is cheap
    and avoids the "early spring" vs "late spring" ambiguity the
    one-word version produced in pilot.
    """
    for wp in gpx_stats.waypoints:
        if wp.time is None:
            continue
        month_name = wp.time.strftime("%B")  # locale-independent for en_US C locale; tests pin it.
        month = wp.time.month
        hemisphere = "northern" if wp.lat >= 0 else "southern"
        if hemisphere == "northern":
            season = _NORTHERN_SEASON_BY_MONTH[month]
        else:
            # Shift by 6 months: southern Apr (4) → northern Oct (10) → autumn.
            shifted = ((month - 1 + 6) % 12) + 1
            season = _NORTHERN_SEASON_BY_MONTH[shifted]
        date_iso = wp.time.strftime("%Y-%m-%d")
        season_phrase = f"{season} ({month_name}; {hemisphere} hemisphere)"
        return date_iso, season_phrase
    return "unknown", "unknown"


def extract_ledger(
    hike_input: HikeInput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    *,
    client: AnthropicClient,
    location: str = "the trail",
) -> FactLedger:
    """Extract a structured :class:`FactLedger` from raw hike inputs.

    The first pass of the two-pass narrative pipeline introduced in
    ADR-009. A cheap, fast model (default ``claude-haiku-4-5``, see
    ``Settings.ledger_model``) reads the seed text + photo timestamps and
    emits the people / weather / chronology subset; this function then
    merges in deterministic GPX-derived fields and validates the whole.

    Args:
        hike_input: Hiker's seed text and source paths.
        gpx_stats: Parsed GPX stats — used both as deterministic facts
            (distances, summit, duration) and for date/season inference.
        photos: Loaded photos. Their first and last timestamps bracket the
            chronology so the extractor can place beats on the
            morning/afternoon axis even when the seed is elliptical.
            The list must be non-empty.
        client: Anthropic client wrapper, typically constructed with the
            cheap ``Settings.ledger_model``. Injected so tests can mock.
        location: Fallback place name when ``hike_input.location_name``
            is unset.

    Returns:
        Validated :class:`FactLedger` — the single input the writer pass
        receives.

    Raises:
        LedgerExtractionError: photos list empty, LLM errored, the model
            returned non-JSON twice in a row, or the parsed JSON did not
            validate against :class:`_ExtractorOutput`.
    """
    if not photos:
        raise LedgerExtractionError("at least one photo is required to extract a ledger")

    place = hike_input.location_name or location
    hike_date, season = _infer_date_and_season(gpx_stats)
    when = _hike_start_datetime(gpx_stats)

    first_photo_time = photos[0].timestamp.isoformat(timespec="minutes")
    last_photo_time = photos[-1].timestamp.isoformat(timespec="minutes")

    # Phase 3 / ADR-010: photo descriptions from the vision pass flow
    # into the extractor as a JSON array, in time order. Empty array when
    # the vision pass is disabled or returned no useful detail; the
    # extractor's prompt explains the fallback so the model knows it's
    # then doing pure seed-only grounding (the Phase 2 contract).
    photo_descriptions: list[dict[str, Any]] = [
        p.description.model_dump(mode="json") for p in photos if p.description is not None
    ]
    photo_descriptions_json = json.dumps(photo_descriptions, ensure_ascii=False, indent=2)

    base_prompt = USER_LEDGER_EXTRACTOR_TEMPLATE.format(
        location=place,
        hike_date=hike_date,
        season=season,
        distance_km=gpx_stats.distance_km,
        duration_min=gpx_stats.duration_min,
        n_photos=len(photos),
        first_photo_time=first_photo_time,
        last_photo_time=last_photo_time,
        seed_text=hike_input.seed_text,
        photo_descriptions_json=photo_descriptions_json,
    )

    parsed = _call_and_parse_with_system(client, base_prompt, SYSTEM_LEDGER_EXTRACTOR)
    if parsed is None:
        logger.warning("ledger response did not parse as JSON; retrying with explicit directive")
        retry_prompt = base_prompt + USER_LEDGER_RETRY_SUFFIX
        parsed = _call_and_parse_with_system(client, retry_prompt, SYSTEM_LEDGER_EXTRACTOR)
        if parsed is None:
            raise LedgerExtractionError(
                "Ledger extractor returned non-JSON output on both attempts."
            )

    try:
        extracted = _ExtractorOutput.model_validate(parsed)
    except ValidationError as exc:
        raise LedgerExtractionError(
            f"Extractor JSON did not match _ExtractorOutput schema: {exc}"
        ) from exc

    # ADR-015: deterministic ledger expansion. Track-derived fields flow
    # from GpxStats; daylight_context is computed from the first / last
    # timed waypoints (falls back to "unknown" if astral can't resolve);
    # day_of_week from ``when``; photo_positions correlates each photo
    # to a waypoint via GPS or timestamp.
    daylight = _compute_daylight(gpx_stats)
    day_of_week = when.strftime("%A") if when.year != 1970 else "unknown"
    photo_positions = correlate_photos_to_track(photos, gpx_stats)

    return FactLedger(
        people=extracted.people,
        weather=extracted.weather,
        chronology=extracted.chronology,
        # ADR-016: re-verify the extractor's "verbatim" claim in Python so
        # the writer only ever sees genuine seed substrings.
        verbatim_user_phrases=_filter_verbatim_phrases(
            extracted.verbatim_user_phrases, hike_input.seed_text
        ),
        where=place,
        when=when,
        season=season,
        duration_min=gpx_stats.duration_min,
        distance_km=gpx_stats.distance_km,
        elevation_gain_m=gpx_stats.elevation_gain_m,
        summit_elev_m=gpx_stats.summit_elev_m,
        n_photos=len(photos),
        # ADR-015 fields below
        track_name=gpx_stats.track_name,
        track_shape=gpx_stats.track_shape,
        elevation_loss_m=gpx_stats.elevation_loss_m,
        day_of_week=day_of_week,
        daylight_context=daylight,
        pauses=list(gpx_stats.pauses),
        photo_positions=photo_positions,
    )


def _compute_daylight(gpx_stats: GpxStats) -> str:
    """Wrap :func:`trailstory.daylight.daylight_context` over the first
    and last timed waypoints, falling back to ``"unknown"`` when the
    GPX has no usable temporal+spatial anchors.
    """
    first_timed = _first_timed_waypoint(gpx_stats.waypoints)
    last_timed = _last_timed_waypoint(gpx_stats.waypoints)
    if (
        first_timed is None
        or last_timed is None
        or first_timed.time is None
        or last_timed.time is None
    ):
        return "unknown"
    return _daylight_context(
        lat=first_timed.lat,
        lon=first_timed.lon,
        start=first_timed.time,
        end=last_timed.time,
    )


def _first_timed_waypoint(waypoints: list[Waypoint]) -> Waypoint | None:
    for wp in waypoints:
        if wp.time is not None:
            return wp
    return None


def _last_timed_waypoint(waypoints: list[Waypoint]) -> Waypoint | None:
    for wp in reversed(waypoints):
        if wp.time is not None:
            return wp
    return None


def generate_narrative(
    hike_input: HikeInput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    *,
    client: AnthropicClient,
    ledger_client: AnthropicClient,
    location: str = "the trail",
    use_cache: bool = True,
    max_inferred_ratio: float | None = 0.5,
) -> NarrativeOutput:
    """Generate a tri-lingual narrative via the two-pass pipeline (ADR-009).

    Pass 1: :func:`extract_ledger` (cheap model) builds a structured
    :class:`FactLedger` from seed + GPX + photo timestamps.
    Pass 2: the writer model receives the ledger as its sole input and
    produces a :class:`NarrativeOutput`.

    Args:
        hike_input: Hiker's seed text and source paths.
        gpx_stats: Parsed GPX stats (distance, elevation, duration, summit).
        photos: Loaded photos. The writer selects 6-8 indices into this list,
            so the list must be non-empty.
        client: Anthropic client wrapper for the WRITER pass (typically the
            Opus-class model from ``Settings.model``). Injected so tests can mock.
        ledger_client: Anthropic client wrapper for the EXTRACTOR pass
            (typically the Haiku-class model from ``Settings.ledger_model``).
            Two clients because each pass has its own model identifier; the
            writer's quality matters more than the extractor's.
        location: Fallback place name. ``hike_input.location_name`` wins when
            set; this kwarg is the default the prompt sees otherwise.
        use_cache: When ``True`` (the default), check the on-disk cache
            first and write any newly generated narrative back to it.
            Set to ``False`` for tests that need to assert call counts on
            the mocked client, and from the CLI's ``--no-cache`` flag.
        max_inferred_ratio: Phase 2.5 / ADR-011 verifier ceiling. If the
            writer's first draft tags more than this share of sentences as
            INFERRED, regenerate once with feedback. ``None`` disables the
            verifier (one writer call, no checks). Default ``0.5`` keeps
            the writer honest without being so strict it produces stiff
            prose. Settable from the CLI / web layer; tests can pin to
            ``None`` to assert call counts.

    Returns:
        Validated ``NarrativeOutput``.

    Raises:
        NarrativeGenerationError: photos list empty, either LLM call failed,
            either pass returned non-JSON twice in a row, or the JSON did
            not validate against its expected schema.
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

    try:
        ledger = extract_ledger(
            hike_input,
            gpx_stats,
            photos,
            client=ledger_client,
            location=location,
        )
    except LedgerExtractionError as exc:
        # Funnel into NarrativeGenerationError so callers above the LLM
        # layer only handle one exception type. The original chain is
        # preserved via __cause__.
        raise NarrativeGenerationError(f"ledger extraction failed: {exc}") from exc

    ledger_json = json.dumps(ledger.model_dump(mode="json"), ensure_ascii=False, indent=2)
    base_prompt = USER_NARRATIVE_TEMPLATE.format(
        ledger_json=ledger_json,
        n_photos=len(photos),
        n_photos_minus_1=len(photos) - 1,
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

    # Phase 2.5 / ADR-011: self-reported provenance verifier. Compute the
    # share of sentences the writer tagged INFERRED; if it exceeds the
    # configured ceiling, regenerate once with feedback that asks the
    # writer to lean harder on grounded sentences. Cheap (no extra LLM
    # call at this point) and uses Phase 4's structured tags directly.
    if max_inferred_ratio is not None and max_inferred_ratio < 1.0:
        ratio = _inferred_ratio(narrative)
        if ratio > max_inferred_ratio:
            logger.warning(
                "writer self-reported inferred_ratio=%.2f > ceiling %.2f; regenerating",
                ratio,
                max_inferred_ratio,
            )
            feedback = _verifier_feedback(ratio, max_inferred_ratio)
            regen_prompt = base_prompt + feedback
            regen_parsed = _call_and_parse(client, regen_prompt)
            if regen_parsed is not None:
                try:
                    regen = NarrativeOutput.model_validate(regen_parsed)
                except ValidationError as exc:
                    logger.warning(
                        "verifier-regenerated draft failed schema validation (%s); "
                        "keeping the original",
                        exc,
                    )
                else:
                    new_ratio = _inferred_ratio(regen)
                    # Only swap in the regenerated draft if it actually
                    # improved the ratio — otherwise the noise of a fresh
                    # call has produced something equivalent or worse and
                    # the original is the safer pick.
                    if new_ratio < ratio:
                        logger.info(
                            "verifier accepted regen: inferred_ratio %.2f -> %.2f",
                            ratio,
                            new_ratio,
                        )
                        narrative = regen
                    else:
                        logger.info(
                            "verifier rejected regen: ratio %.2f did not improve on %.2f",
                            new_ratio,
                            ratio,
                        )

    # ADR-016: verbatim-phrase verifier. Same free-signal pattern as the
    # ratio check above — detection costs nothing (substring scan), only
    # the conditional regen is paid. If the ledger carries verbatim
    # phrases and none of them surfaced in any language's prose, ask the
    # writer once more with the phrases named; keep the regen only if it
    # actually uses one (improvement-only admission, mirroring ADR-011).
    # Streaming is bypassed, same as the ratio verifier.
    if ledger.verbatim_user_phrases and not _uses_verbatim_phrase(
        narrative, ledger.verbatim_user_phrases
    ):
        logger.warning(
            "writer used none of %d verbatim user phrase(s); regenerating",
            len(ledger.verbatim_user_phrases),
        )
        feedback = VERIFIER_VERBATIM_FEEDBACK_TEMPLATE.format(
            phrases=", ".join(f'"{p}"' for p in ledger.verbatim_user_phrases)
        )
        regen_parsed = _call_and_parse(client, base_prompt + feedback)
        if regen_parsed is not None:
            try:
                regen = NarrativeOutput.model_validate(regen_parsed)
            except ValidationError as exc:
                logger.warning(
                    "verbatim-regenerated draft failed schema validation (%s); "
                    "keeping the original",
                    exc,
                )
            else:
                if _uses_verbatim_phrase(regen, ledger.verbatim_user_phrases):
                    logger.info("verbatim verifier accepted regen")
                    narrative = regen
                else:
                    logger.info("verbatim verifier rejected regen: still no phrase used")

    if key is not None:
        narrative_cache.put(key, narrative)
    return narrative


def _inferred_ratio(narrative: NarrativeOutput) -> float:
    """Self-reported INFERRED-sentence share. Used by the Phase 2.5 verifier.

    Returns ``0.0`` for an empty narrative (defensive — the schema
    requires paragraphs, so this should not happen in practice).
    """
    total = 0
    inferred = 0
    for paragraph in narrative.paragraphs:
        for sentence in paragraph:
            total += 1
            if sentence.provenance.source == ProvenanceSource.INFERRED:
                inferred += 1
    if total == 0:
        return 0.0
    return inferred / total


def _uses_verbatim_phrase(narrative: NarrativeOutput, phrases: list[str]) -> bool:
    """True when any ledger phrase appears literally in the narrative.

    Scans all three languages: the contract asks for the phrase verbatim
    in the language the hiker wrote it in (a Russian seed lands in the RU
    text), and only a faithful rendering — which no substring check can
    verify — in the other two. Title, subtitle, pull quote, and milestone
    count too, so a phrase surfaced as the pull quote satisfies the
    contract. Case-insensitive, same as :func:`_filter_verbatim_phrases`.
    """
    flat = narrative.paragraphs_as_localized()
    haystack = " ".join(
        [
            *flat.en,
            *flat.ru,
            *flat.de,
            narrative.title.en,
            narrative.title.ru,
            narrative.title.de,
            narrative.subtitle.en,
            narrative.subtitle.ru,
            narrative.subtitle.de,
            narrative.pull_quote.en,
            narrative.pull_quote.ru,
            narrative.pull_quote.de,
            narrative.milestone.en,
            narrative.milestone.ru,
            narrative.milestone.de,
        ]
    ).casefold()
    return any(p.casefold() in haystack for p in phrases)


def _verifier_feedback(observed: float, ceiling: float) -> str:
    """Build the regeneration-feedback suffix for the writer prompt."""
    return (
        f"\n\nYour previous draft tagged {observed:.0%} of its sentences as "
        f"INFERRED, exceeding the {ceiling:.0%} ceiling. Rewrite with more "
        f"sentences whose provenance is seed / photo / gpx — preserve the "
        f"chronology and voice, but lean harder on what the ledger actually "
        f"says rather than literary reconstruction. Output JSON in the same "
        f"shape as before, no markdown fences, no commentary."
    )


def generate_narrative_stream(
    hike_input: HikeInput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    *,
    client: AnthropicClient,
    ledger_client: AnthropicClient,
    location: str = "the trail",
) -> Iterator[NarrativeStreamEvent]:
    """Streaming variant of :func:`generate_narrative` (ADR-009).

    The ledger-extraction pass runs synchronously up front (small Haiku
    call, ~1-2s) and produces no stream events; the writer pass streams
    chunks to the UI as before. The first byte of streamed prose is
    therefore delayed by the extractor's latency — acceptable in
    exchange for the structural fabrication guard. The web builder UI
    already shows a "Generating…" spinner during the pre-stream window;
    extractor latency lands inside that.

    Yields a :class:`NarrativeStreamChunk` for every text delta from the
    writer LLM, optionally a :class:`NarrativeStreamRetry` between
    attempts when the first response did not parse, and finally a
    :class:`NarrativeStreamComplete` with the validated
    :class:`NarrativeOutput`.

    The retry policy mirrors :func:`generate_narrative`: one extra attempt
    with :data:`USER_NARRATIVE_RETRY_SUFFIX` when the first response was
    unparseable. Schema-validation failures are not retried — they raise
    :class:`NarrativeGenerationError` immediately, the same as the
    non-streaming pipeline. Ledger-extraction failures are funnelled into
    :class:`NarrativeGenerationError` so callers above the LLM layer only
    handle one exception type.

    The cache is intentionally bypassed for streaming runs: the user is
    looking at a "writing your story" page and expects to see the words
    appear, so a cached instant return would be jarring. The
    non-streaming :func:`generate_narrative` keeps the cache for CLI use.

    Args:
        hike_input: Hiker's seed text and source paths.
        gpx_stats: Parsed GPX stats.
        photos: Loaded photos. The writer selects 6-8 indices into this list.
        client: Anthropic client wrapper for the WRITER pass. Must
            implement ``complete_stream``.
        ledger_client: Anthropic client wrapper for the EXTRACTOR pass.
            Uses ``complete`` (non-streaming); a small JSON dict doesn't
            benefit from streaming.
        location: Fallback place name when ``hike_input.location_name`` is
            unset.

    Yields:
        :class:`NarrativeStreamEvent` instances. The terminal event is
        always :class:`NarrativeStreamComplete` on success.

    Raises:
        NarrativeGenerationError: photos list empty, either LLM call failed,
            either pass returned non-JSON twice in a row, or the JSON did
            not validate against its expected schema.
    """
    if not photos:
        raise NarrativeGenerationError("at least one photo is required to build a narrative")

    try:
        ledger = extract_ledger(
            hike_input,
            gpx_stats,
            photos,
            client=ledger_client,
            location=location,
        )
    except LedgerExtractionError as exc:
        raise NarrativeGenerationError(f"ledger extraction failed: {exc}") from exc

    ledger_json = json.dumps(ledger.model_dump(mode="json"), ensure_ascii=False, indent=2)
    base_prompt = USER_NARRATIVE_TEMPLATE.format(
        ledger_json=ledger_json,
        n_photos=len(photos),
        n_photos_minus_1=len(photos) - 1,
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
    """Call the writer LLM once and try to parse the response as a JSON object.

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


def _call_and_parse_with_system(
    client: AnthropicClient, prompt: str, system: str
) -> dict[str, Any] | None:
    """Like :func:`_call_and_parse` but with a caller-supplied system prompt.

    Used by :func:`extract_ledger` (Phase 2) which needs the
    extractor-specific persona instead of the writer's. Client-level
    errors are funnelled into :class:`LedgerExtractionError` here — the
    caller decides whether to retry the model on parse failure.
    """
    try:
        raw = client.complete(prompt=prompt, system=system)
    except (LLMResponseError, LLMRetryExhaustedError) as exc:
        raise LedgerExtractionError(f"Ledger LLM call failed: {exc}") from exc

    cleaned = _strip_code_fences(raw)
    try:
        result = json.loads(cleaned)
    except JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _hike_start_datetime(gpx_stats: GpxStats) -> datetime:
    """Return the first timed waypoint's datetime, or epoch as fallback.

    The :class:`FactLedger.when` field is required and typed as
    ``datetime``; manually-edited GPX files that strip timestamps would
    otherwise force us to make the field optional everywhere. Picking
    UTC epoch keeps the type stable; the orchestrator and prompt already
    surface "unknown" date/season text via
    :func:`_infer_date_and_season` so callers downstream see the
    semantically-correct unknown signal.
    """
    for wp in gpx_stats.waypoints:
        if wp.time is not None:
            return wp.time
    return datetime(1970, 1, 1, tzinfo=UTC)


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
