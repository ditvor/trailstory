"""Tests for ``trailstory.llm.narrative``.

Every test injects a ``MagicMock(spec=AnthropicClient)`` — no real network
calls, per CLAUDE.md ("Always mocks the Anthropic client. Never calls the
real API.").
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.conftest import paragraphs_dict_from_strings
from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.llm.narrative import (
    NarrativeGenerationError,
    NarrativeStreamChunk,
    NarrativeStreamComplete,
    NarrativeStreamRetry,
    generate_narrative,
    generate_narrative_stream,
)
from trailstory.llm.prompts import (
    USER_NARRATIVE_RETRY_SUFFIX,
    USER_NARRATIVE_TEMPLATE,
)
from trailstory.models import GpxStats, HikeInput, NarrativeOutput, PhotoMeta, Waypoint

# ── fixtures ─────────────────────────────────────────────────────────────────


def _hike_input() -> HikeInput:
    return HikeInput(
        gpx_path=Path("/tmp/hike.gpx"),
        photos_dir=Path("/tmp/photos"),
        seed_text="The fog cleared just as we reached the ridge.",
    )


def _gpx_stats(
    *,
    waypoint_time: datetime | None = datetime(2026, 4, 18, 10, 25, 0),
    lat: float = 47.55,
) -> GpxStats:
    """Build a GpxStats fixture. ``waypoint_time`` and ``lat`` are
    parameterised so tests can exercise the date/season inference under
    no-timestamps and southern-hemisphere conditions (ADR-008)."""
    return GpxStats(
        distance_km=6.2,
        elevation_gain_m=610,
        duration_min=165,
        start_elev_m=720.0,
        summit_elev_m=1330.0,
        waypoints=[
            Waypoint(lat=lat, lon=11.78, ele_m=720.0, time=waypoint_time),
            Waypoint(lat=lat + 0.01, lon=11.79, ele_m=1330.0, time=waypoint_time),
        ],
    )


def _photos(n: int = 12) -> list[PhotoMeta]:
    return [
        PhotoMeta(
            path=Path(f"/tmp/photos/{i:02d}.jpg"),
            timestamp=datetime(2025, 8, 15, 9 + i // 4, (i * 13) % 60, 0),
            index=i,
        )
        for i in range(n)
    ]


def _valid_response_dict(indices: list[int] | None = None) -> dict[str, object]:
    return {
        "schema_version": 4,
        "title": {
            "en": "Above the fog line",
            "ru": "Над линией тумана",
            "de": "Über der Nebelgrenze",
        },
        "subtitle": {
            "en": "A morning above the cloud sea",
            "ru": "Утро над морем облаков",
            "de": "Ein Morgen über dem Wolkenmeer",
        },
        "paragraphs": paragraphs_dict_from_strings(
            en=[
                "We left the trailhead at first light.",
                "By the saddle the cloud was thinning.",
                "Mia slept the whole climb, her cheek warm against the carrier.",
            ],
            ru=[
                # noqa lines: "с" and "К" are genuine single-letter Russian
                # prepositions; ruff flags them as Cyrillic-Latin lookalikes
                # (RUF001), but they are correct Russian here.
                "Вышли на тропу с первыми лучами.",  # noqa: RUF001
                "К седловине облака начали редеть.",  # noqa: RUF001
                "Мия проспала весь подъём, прижавшись щекой к переноске.",
            ],
            de=[
                "Bei erstem Licht brachen wir auf.",
                "Am Sattel begann die Wolke sich zu lichten.",
                "Mia schlief den ganzen Aufstieg, die Wange warm an der Trage.",
            ],
        ),
        "pull_quote": {
            "en": "The fog cleared just as we reached the ridge.",
            "ru": "Туман рассеялся как раз когда мы вышли на хребет.",
            "de": "Der Nebel lichtete sich, gerade als wir den Grat erreichten.",
        },
        "milestone": {
            "en": "First mountain hike",
            "ru": "Первый горный поход",
            "de": "Erste Bergwanderung",
        },
        "selected_photo_indices": indices or [0, 2, 4, 6, 8, 10],
    }


def _valid_response_json(indices: list[int] | None = None) -> str:
    return json.dumps(_valid_response_dict(indices))


def _client(*responses: str | Exception) -> MagicMock:
    """Build a mocked WRITER client whose ``.complete`` yields each item in turn."""
    mock = MagicMock(spec=AnthropicClient)
    mock.complete.side_effect = list(responses)
    # ``cache_key`` reads ``client.model``; with spec=AnthropicClient that
    # would be a MagicMock and json.dumps would fail. Pin it to a string
    # so any test that does opt into the cache still works.
    mock.model = "claude-opus-4-7-test"
    return mock


def _valid_extractor_dict(
    *,
    people: list[dict[str, object]] | None = None,
    weather: str = "amazing weather",
    chronology: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build a plausible extractor response for ADR-009 two-pass tests."""
    return {
        "people": people if people is not None else [{"name": "Mia", "role": "baby in carrier"}],
        "weather": weather,
        "chronology": chronology
        if chronology is not None
        else [
            {
                "time_of_day": "morning",
                "activity": "ascent through fog",
                "emotion": "anticipation",
                "objects_mentioned": ["fog", "ridge"],
            },
            {
                "time_of_day": "afternoon",
                "activity": "summit and descent",
                "emotion": "quiet",
                "objects_mentioned": ["sunlight"],
            },
        ],
    }


def _valid_extractor_json(**overrides: object) -> str:
    return json.dumps(_valid_extractor_dict(**overrides))


def _ledger_client(*responses: str | Exception) -> MagicMock:
    """Build a mocked LEDGER (extractor) client.

    ADR-009: every call to ``generate_narrative`` / ``generate_narrative_stream``
    now needs two mocked clients — one for the cheap extractor pass and one
    for the Opus writer. Default response is the standard valid extractor
    output; callers can pass exception types or non-JSON strings to exercise
    failure paths.
    """
    mock = MagicMock(spec=AnthropicClient)
    if not responses:
        responses = (_valid_extractor_json(),)
    mock.complete.side_effect = list(responses)
    mock.model = "claude-haiku-4-5-test"
    return mock


# Every test in this module exercises the LLM orchestration path with
# ``use_cache=False``: we're testing prompt assembly, parse/retry, and
# validation, not the cache. Cache behaviour is covered separately in
# ``tests/test_cache.py``. Disabling here also avoids hashing the fake
# ``/tmp/hike.gpx`` paths that these fixtures use.
_NO_CACHE: dict[str, bool] = {"use_cache": False}


# ── happy path ───────────────────────────────────────────────────────────────


def test_generate_narrative_happy_path() -> None:
    client = _client(_valid_response_json())

    out = generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    assert isinstance(out, NarrativeOutput)
    assert out.title.en == "Above the fog line"
    assert out.title.ru == "Над линией тумана"
    assert out.title.de == "Über der Nebelgrenze"
    assert out.selected_photo_indices == [0, 2, 4, 6, 8, 10]
    assert client.complete.call_count == 1


def test_generate_narrative_strips_markdown_fences() -> None:
    """Models often wrap JSON in ```json``` despite being told not to."""
    fenced = "```json\n" + _valid_response_json() + "\n```"
    client = _client(fenced)

    out = generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    assert out.title.en == "Above the fog line"
    # Fenced response parsed on the first attempt — no retry needed.
    assert client.complete.call_count == 1


def test_generate_narrative_strips_bare_triple_backtick_fence() -> None:
    fenced = "```\n" + _valid_response_json() + "\n```"
    client = _client(fenced)

    out = generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    assert out.milestone.en == "First mountain hike"
    assert client.complete.call_count == 1


# ── prompt wiring ────────────────────────────────────────────────────────────


def test_generate_narrative_passes_hike_data_to_prompt() -> None:
    client = _client(_valid_response_json())

    generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(n=8),
        client=client,
        ledger_client=_ledger_client(),
        location="Tegernsee, Bavaria",
        **_NO_CACHE,
    )

    sent = client.complete.call_args.kwargs["prompt"]
    # Under ADR-009 the writer receives a serialized FactLedger plus only
    # n_photos / n_photos_minus_1. The ledger JSON embedded in the prompt
    # carries the previously-individual hike-data fields, so they still
    # appear as substrings — but now via JSON, not raw placeholders.
    assert "Tegernsee, Bavaria" in sent  # ledger["where"]
    assert "6.2" in sent  # ledger["distance_km"]
    assert "610" in sent  # ledger["elevation_gain_m"]
    assert "1330" in sent  # ledger["summit_elev_m"]
    assert "165" in sent  # ledger["duration_min"]
    # Photo count and zero-indexed upper bound — still direct placeholders.
    assert "8 available (indexed 0-7)" in sent
    # ADR-008 date + season grounding now lives inside the ledger.
    assert "2026-04-18" in sent  # ledger["when"]
    assert "spring (April; northern hemisphere)" in sent  # ledger["season"]
    # The raw seed text no longer reaches the writer — that's the whole
    # point of the two-pass architecture (ADR-009).
    assert "fog cleared" not in sent


def test_generate_narrative_uses_default_location_when_not_supplied() -> None:
    client = _client(_valid_response_json())

    generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    sent = client.complete.call_args.kwargs["prompt"]
    assert "the trail" in sent


def test_generate_narrative_prefers_hike_input_location_name() -> None:
    """``HikeInput.location_name`` overrides the ``location`` kwarg fallback."""
    client = _client(_valid_response_json())
    hike = _hike_input().model_copy(update={"location_name": "Watzmann"})

    generate_narrative(
        hike,
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        location="ignored",
        **_NO_CACHE,
    )

    sent = client.complete.call_args.kwargs["prompt"]
    assert "Watzmann" in sent
    assert "ignored" not in sent


def test_generate_narrative_passes_system_prompt() -> None:
    client = _client(_valid_response_json())

    generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    system = client.complete.call_args.kwargs["system"]
    assert "warm" in system.lower() or "memories" in system.lower()


# ── retry on JSON-parse failure ──────────────────────────────────────────────


def test_generate_narrative_retries_on_invalid_json_then_succeeds() -> None:
    prose = "Sure! Here's the memory you asked for: it was a beautiful day..."
    client = _client(prose, _valid_response_json())

    out = generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    assert out.title.en == "Above the fog line"
    assert client.complete.call_count == 2


def test_generate_narrative_retry_appends_json_only_directive() -> None:
    client = _client("not json at all", _valid_response_json())

    generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    first_prompt = client.complete.call_args_list[0].kwargs["prompt"]
    second_prompt = client.complete.call_args_list[1].kwargs["prompt"]
    assert second_prompt == first_prompt + USER_NARRATIVE_RETRY_SUFFIX


def test_generate_narrative_raises_after_two_invalid_json_attempts() -> None:
    client = _client("first prose", "second prose")

    with pytest.raises(NarrativeGenerationError, match="non-JSON output on both"):
        generate_narrative(
            _hike_input(),
            _gpx_stats(),
            _photos(),
            client=client,
            ledger_client=_ledger_client(),
            **_NO_CACHE,
        )
    assert client.complete.call_count == 2


def test_generate_narrative_treats_json_array_as_parse_failure() -> None:
    """Top-level JSON array is well-formed JSON but not a NarrativeOutput
    object — should trigger the retry path, not a validation error."""
    client = _client("[1, 2, 3]", _valid_response_json())

    out = generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    assert out.title.en == "Above the fog line"
    assert client.complete.call_count == 2


# ── validation failures (no retry per spec) ──────────────────────────────────


def test_generate_narrative_raises_on_validation_error() -> None:
    """JSON parses but is missing a required field — surface immediately,
    no retry (per Step 7 spec, retry is only for parse failures)."""
    incomplete = json.dumps({"title": {"en": "x", "ru": "y", "de": "z"}})  # missing required
    client = _client(incomplete)

    with pytest.raises(NarrativeGenerationError, match="schema"):
        generate_narrative(
            _hike_input(),
            _gpx_stats(),
            _photos(),
            client=client,
            ledger_client=_ledger_client(),
            **_NO_CACHE,
        )
    assert client.complete.call_count == 1


def test_generate_narrative_does_not_retry_on_validation_error() -> None:
    """Belt-and-braces version of the above: even if a 2nd response was
    queued, validation failure must not consume it."""
    incomplete = json.dumps({"title": {"en": "x", "ru": "y", "de": "z"}})
    second = _valid_response_json()
    client = _client(incomplete, second)

    with pytest.raises(NarrativeGenerationError):
        generate_narrative(
            _hike_input(),
            _gpx_stats(),
            _photos(),
            client=client,
            ledger_client=_ledger_client(),
            **_NO_CACHE,
        )
    assert client.complete.call_count == 1


# ── client-level errors ──────────────────────────────────────────────────────


def test_generate_narrative_translates_llm_response_error() -> None:
    client = _client(LLMResponseError("empty response"))

    with pytest.raises(NarrativeGenerationError, match="LLM call failed"):
        generate_narrative(
            _hike_input(),
            _gpx_stats(),
            _photos(),
            client=client,
            ledger_client=_ledger_client(),
            **_NO_CACHE,
        )


def test_generate_narrative_translates_llm_retry_exhausted() -> None:
    client = _client(LLMRetryExhaustedError("rate-limited 3x"))

    with pytest.raises(NarrativeGenerationError, match="LLM call failed"):
        generate_narrative(
            _hike_input(),
            _gpx_stats(),
            _photos(),
            client=client,
            ledger_client=_ledger_client(),
            **_NO_CACHE,
        )


def test_generate_narrative_does_not_retry_on_llm_error() -> None:
    """Client-level errors are not retried at this layer; the client owns
    its own retry policy. A second queued response must not be consumed."""
    client = _client(LLMResponseError("boom"), _valid_response_json())

    with pytest.raises(NarrativeGenerationError):
        generate_narrative(
            _hike_input(),
            _gpx_stats(),
            _photos(),
            client=client,
            ledger_client=_ledger_client(),
            **_NO_CACHE,
        )
    assert client.complete.call_count == 1


# ── input guards ─────────────────────────────────────────────────────────────


def test_generate_narrative_rejects_empty_photo_list() -> None:
    client = _client()  # should never be called

    with pytest.raises(NarrativeGenerationError, match="at least one photo"):
        generate_narrative(
            _hike_input(),
            _gpx_stats(),
            [],
            client=client,
            ledger_client=_ledger_client(),
            **_NO_CACHE,
        )
    client.complete.assert_not_called()


def test_generate_narrative_photo_count_matches_photos() -> None:
    """``n_photos`` placeholder reflects the actual list length."""
    client = _client(_valid_response_json())

    generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(n=4),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    sent = client.complete.call_args.kwargs["prompt"]
    # ADR-009 writer prompt phrasing: "{n_photos} available (indexed 0-{n_photos_minus_1})".
    assert "4 available (indexed 0-3)" in sent


def test_generate_narrative_supplies_every_template_placeholder() -> None:
    """Verifies the orchestrator covers every documented placeholder.

    If someone adds a placeholder to USER_NARRATIVE_TEMPLATE without
    updating the orchestrator, ``.format`` raises KeyError and this test
    fails — catching the drift before production does.
    """
    from string import Formatter

    expected = {name for _, name, _, _ in Formatter().parse(USER_NARRATIVE_TEMPLATE) if name}
    client = _client(_valid_response_json())

    # If any placeholder is unsupplied, .format() inside generate_narrative
    # raises KeyError, which propagates (not wrapped in NarrativeGenerationError).
    generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )
    sent = client.complete.call_args.kwargs["prompt"]

    # Sanity check: no remaining {placeholder} tokens.
    import re

    leftover = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", sent)
    assert leftover == [], f"unfilled placeholders {leftover}; expected none of {expected}"


# ── date + season inference (ADR-008, surfaced via ADR-009 ledger) ───────────
#
# Phase 1 (ADR-008) introduced the inferred date/season; Phase 2 (ADR-009)
# threads them through the FactLedger now rather than as raw writer-prompt
# placeholders. These tests assert the inference still flows correctly —
# they look at the LEDGER prompt (where the season string is interpolated
# directly into the extractor's grounding context) and at the WRITER
# prompt's embedded ledger JSON.


def test_generate_narrative_grounds_northern_spring_in_prompt() -> None:
    client = _client(_valid_response_json())
    ledger_client = _ledger_client()
    stats = _gpx_stats(waypoint_time=datetime(2026, 4, 18, 10, 25, 0), lat=47.55)

    generate_narrative(
        _hike_input(),
        stats,
        _photos(),
        client=client,
        ledger_client=ledger_client,
        **_NO_CACHE,
    )

    # Extractor sees the date+season as grounding context.
    ledger_sent = ledger_client.complete.call_args.kwargs["prompt"]
    assert "2026-04-18" in ledger_sent
    assert "spring (April; northern hemisphere)" in ledger_sent
    # Writer sees them inside the serialized FactLedger JSON.
    writer_sent = client.complete.call_args.kwargs["prompt"]
    assert "2026-04-18" in writer_sent
    assert "spring (April; northern hemisphere)" in writer_sent


def test_generate_narrative_grounds_southern_hemisphere_in_prompt() -> None:
    """A negative latitude inverts the season — April in Patagonia is autumn."""
    client = _client(_valid_response_json())
    ledger_client = _ledger_client()
    stats = _gpx_stats(waypoint_time=datetime(2026, 4, 18, 10, 25, 0), lat=-41.5)

    generate_narrative(
        _hike_input(),
        stats,
        _photos(),
        client=client,
        ledger_client=ledger_client,
        **_NO_CACHE,
    )

    # Both passes see the southern-hemisphere season.
    assert (
        "autumn (April; southern hemisphere)" in ledger_client.complete.call_args.kwargs["prompt"]
    )
    assert "autumn (April; southern hemisphere)" in client.complete.call_args.kwargs["prompt"]


def test_generate_narrative_grounds_no_timestamps_as_unknown() -> None:
    """Manually-edited GPX files sometimes strip timing — fall back to 'unknown'."""
    client = _client(_valid_response_json())
    ledger_client = _ledger_client()
    stats = _gpx_stats(waypoint_time=None)

    generate_narrative(
        _hike_input(),
        stats,
        _photos(),
        client=client,
        ledger_client=ledger_client,
        **_NO_CACHE,
    )

    ledger_sent = ledger_client.complete.call_args.kwargs["prompt"]
    assert "Date: unknown (unknown)" in ledger_sent


def test_generate_narrative_writer_prompt_constrains_to_ledger_only() -> None:
    """ADR-009: the writer's prompt instructs ledger-only grounding, and the
    raw seed text never reaches it (that's the structural fabrication guard)."""
    client = _client(_valid_response_json())

    generate_narrative(
        _hike_input(),
        _gpx_stats(),
        _photos(),
        client=client,
        ledger_client=_ledger_client(),
        **_NO_CACHE,
    )

    writer_sent = client.complete.call_args.kwargs["prompt"]
    # Ledger-only constraint phrasing.
    assert "ledger" in writer_sent.lower()
    # The original Phase 1 anti-fabrication example noun set survives.
    assert "duck" in writer_sent.lower() or "animal" in writer_sent.lower()
    # The seed text does NOT reach the writer (would let it ground in raw
    # prose, defeating the point).
    assert "fog cleared" not in writer_sent


# ── streaming variant ────────────────────────────────────────────────────────


def _stream_client(*responses: list[str] | Exception) -> MagicMock:
    """Build a mocked client whose ``.complete_stream`` yields each list."""
    mock = MagicMock(spec=AnthropicClient)

    def _side_effect(*_a: object, **_kw: object) -> object:
        item = mock._stream_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return iter(item)

    mock._stream_responses = list(responses)
    mock.complete_stream.side_effect = _side_effect
    mock.model = "claude-opus-4-7-test"
    return mock


def _split(payload: str, parts: int = 6) -> list[str]:
    step = max(1, len(payload) // parts)
    return [payload[i : i + step] for i in range(0, len(payload), step)]


def test_generate_narrative_stream_yields_chunks_and_terminal_event() -> None:
    chunks = _split(_valid_response_json())
    client = _stream_client(chunks)

    events = list(
        generate_narrative_stream(
            _hike_input(), _gpx_stats(), _photos(), client=client, ledger_client=_ledger_client()
        )
    )

    chunk_events = [e for e in events if isinstance(e, NarrativeStreamChunk)]
    complete_events = [e for e in events if isinstance(e, NarrativeStreamComplete)]
    assert len(chunk_events) == len(chunks)
    assert "".join(c.text for c in chunk_events) == "".join(chunks)
    assert len(complete_events) == 1
    narrative = complete_events[0].narrative
    assert isinstance(narrative, NarrativeOutput)
    assert narrative.title.en == "Above the fog line"


def test_generate_narrative_stream_retries_on_unparseable_first_attempt() -> None:
    chunks_bad = _split("this is not json", parts=3)
    chunks_good = _split(_valid_response_json())
    client = _stream_client(chunks_bad, chunks_good)

    events = list(
        generate_narrative_stream(
            _hike_input(), _gpx_stats(), _photos(), client=client, ledger_client=_ledger_client()
        )
    )

    retries = [e for e in events if isinstance(e, NarrativeStreamRetry)]
    completes = [e for e in events if isinstance(e, NarrativeStreamComplete)]
    assert len(retries) == 1
    assert len(completes) == 1
    # Both attempts streamed — chunks from each appear.
    chunk_text = "".join(e.text for e in events if isinstance(e, NarrativeStreamChunk))
    assert "this is not json" in chunk_text
    assert "Above the fog line" in chunk_text


def test_generate_narrative_stream_raises_on_double_failure() -> None:
    bad = _split("still not json", parts=3)
    client = _stream_client(bad, bad)

    with pytest.raises(NarrativeGenerationError, match="non-JSON"):
        list(
            generate_narrative_stream(
                _hike_input(),
                _gpx_stats(),
                _photos(),
                client=client,
                ledger_client=_ledger_client(),
            )
        )


def test_generate_narrative_stream_strips_markdown_fences() -> None:
    fenced = "```json\n" + _valid_response_json() + "\n```"
    client = _stream_client(_split(fenced))

    events = list(
        generate_narrative_stream(
            _hike_input(), _gpx_stats(), _photos(), client=client, ledger_client=_ledger_client()
        )
    )
    # Single attempt — no retry — and the narrative parses cleanly.
    completes = [e for e in events if isinstance(e, NarrativeStreamComplete)]
    assert len(completes) == 1
    assert completes[0].narrative.title.en == "Above the fog line"


def test_generate_narrative_stream_rejects_empty_photo_list() -> None:
    client = _stream_client()
    with pytest.raises(NarrativeGenerationError, match="at least one photo"):
        list(
            generate_narrative_stream(
                _hike_input(), _gpx_stats(), [], client=client, ledger_client=_ledger_client()
            )
        )
    client.complete_stream.assert_not_called()


def test_generate_narrative_stream_translates_llm_error() -> None:
    client = _stream_client(LLMResponseError("boom"))
    with pytest.raises(NarrativeGenerationError, match="LLM call failed"):
        list(
            generate_narrative_stream(
                _hike_input(),
                _gpx_stats(),
                _photos(),
                client=client,
                ledger_client=_ledger_client(),
            )
        )


def test_generate_narrative_stream_translates_retry_exhausted_error() -> None:
    client = _stream_client(LLMRetryExhaustedError("rate limit"))
    with pytest.raises(NarrativeGenerationError, match="LLM call failed"):
        list(
            generate_narrative_stream(
                _hike_input(),
                _gpx_stats(),
                _photos(),
                client=client,
                ledger_client=_ledger_client(),
            )
        )


def test_generate_narrative_stream_validates_schema() -> None:
    """JSON parses but is missing required keys → NarrativeGenerationError."""
    bogus = json.dumps({"title": {"en": "x"}})
    client = _stream_client(_split(bogus, parts=2))
    with pytest.raises(NarrativeGenerationError, match="schema"):
        list(
            generate_narrative_stream(
                _hike_input(),
                _gpx_stats(),
                _photos(),
                client=client,
                ledger_client=_ledger_client(),
            )
        )
