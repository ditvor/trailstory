"""Tests for :mod:`trailstory.llm.narrative.extract_ledger` and :class:`FactLedger`.

ADR-009: Phase 2 of the narrative-faithfulness initiative introduces a
two-pass pipeline. The first pass (``extract_ledger``) is what these tests
exercise — the second pass (``generate_narrative`` / ``...stream``) lives
in :mod:`tests.test_narrative`. All tests mock the Anthropic client per
CLAUDE.md ("Always mocks the Anthropic client. Never calls the real API.").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.llm.narrative import (
    LedgerExtractionError,
    extract_ledger,
)
from trailstory.models import (
    Beat,
    FactLedger,
    GpxStats,
    HikeInput,
    Person,
    PhotoMeta,
    Waypoint,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _hike_input() -> HikeInput:
    return HikeInput(
        gpx_path=Path("/tmp/hike.gpx"),
        photos_dir=Path("/tmp/photos"),
        seed_text="The fog cleared just as we reached the ridge.",
        location_name="Tegernsee, Bavaria",
    )


def _gpx_stats(
    *,
    waypoint_time: datetime | None = datetime(2026, 4, 18, 10, 25, 0, tzinfo=UTC),
    lat: float = 47.55,
) -> GpxStats:
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


def _photos(n: int = 6) -> list[PhotoMeta]:
    return [
        PhotoMeta(
            path=Path(f"/tmp/photos/{i:02d}.jpg"),
            timestamp=datetime(2026, 4, 18, 9 + i, 0, 0, tzinfo=UTC),
            index=i,
        )
        for i in range(n)
    ]


def _valid_extractor_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "people": [{"name": "Mia", "role": "baby in carrier"}],
        "weather": "amazing weather",
        "chronology": [
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
    base.update(overrides)
    return base


def _valid_extractor_json(**overrides: object) -> str:
    return json.dumps(_valid_extractor_dict(**overrides))


def _client(*responses: str | Exception) -> MagicMock:
    mock = MagicMock(spec=AnthropicClient)
    mock.complete.side_effect = list(responses)
    mock.model = "claude-haiku-4-5-test"
    return mock


# ── FactLedger validation ────────────────────────────────────────────────────


def test_fact_ledger_validates_minimum_required_fields() -> None:
    """Smoke test for the Pydantic shape — every required field is checked."""
    ledger = FactLedger(
        people=[Person(name="Mia", role="baby")],
        weather="amazing",
        chronology=[Beat(time_of_day="morning", activity="walking")],
        where="Tegernsee",
        when=datetime(2026, 4, 18, tzinfo=UTC),
        season="spring (April; northern hemisphere)",
        duration_min=165,
        distance_km=6.2,
        elevation_gain_m=610,
        summit_elev_m=1330,
        n_photos=6,
    )
    assert ledger.people[0].name == "Mia"
    assert ledger.chronology[0].objects_mentioned == []  # default factory


def test_fact_ledger_rejects_negative_distance() -> None:
    """``ge=0`` bounds on distance/duration/elevation keep garbage out of the writer."""
    with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
        FactLedger(
            people=[],
            weather="unknown",
            chronology=[],
            where="x",
            when=datetime(2026, 1, 1, tzinfo=UTC),
            season="winter",
            duration_min=10,
            distance_km=-1.0,  # invalid
            elevation_gain_m=0,
            summit_elev_m=100,
            n_photos=1,
        )


def test_fact_ledger_rejects_zero_photos() -> None:
    """``n_photos >= 1`` guards the writer's selected_photo_indices contract."""
    with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
        FactLedger(
            people=[],
            weather="unknown",
            chronology=[],
            where="x",
            when=datetime(2026, 1, 1, tzinfo=UTC),
            season="winter",
            duration_min=10,
            distance_km=1,
            elevation_gain_m=0,
            summit_elev_m=100,
            n_photos=0,
        )


def test_person_role_is_optional() -> None:
    p = Person(name="Igor")
    assert p.role is None


def test_beat_emotion_is_optional() -> None:
    b = Beat(time_of_day="morning", activity="brunch")
    assert b.emotion is None
    assert b.objects_mentioned == []


# ── extract_ledger happy path ────────────────────────────────────────────────


def test_extract_ledger_merges_llm_subset_with_deterministic_fields() -> None:
    """The extractor LLM fills people/weather/chronology; GPX-derived fields
    (where, when, season, distances, summit, photo count) are filled in
    Python and must come through unchanged in the returned ledger."""
    client = _client(_valid_extractor_json())

    ledger = extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)

    # LLM-derived fields preserved.
    assert ledger.people[0].name == "Mia"
    assert ledger.weather == "amazing weather"
    assert len(ledger.chronology) == 2
    # Deterministic fields filled from inputs.
    assert ledger.where == "Tegernsee, Bavaria"
    assert ledger.when == datetime(2026, 4, 18, 10, 25, 0, tzinfo=UTC)
    assert ledger.season == "spring (April; northern hemisphere)"
    assert ledger.duration_min == 165
    assert ledger.distance_km == pytest.approx(6.2)
    assert ledger.summit_elev_m == pytest.approx(1330)
    assert ledger.n_photos == 6


def test_extract_ledger_uses_default_location_when_unset() -> None:
    hike = _hike_input().model_copy(update={"location_name": None})
    client = _client(_valid_extractor_json())

    ledger = extract_ledger(hike, _gpx_stats(), _photos(), client=client, location="default")

    assert ledger.where == "default"


def test_extract_ledger_passes_seed_text_to_llm() -> None:
    """The extractor — unlike the writer — DOES see the raw seed text."""
    client = _client(_valid_extractor_json())

    extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)

    sent = client.complete.call_args.kwargs["prompt"]
    assert "fog cleared" in sent


def test_extract_ledger_passes_photo_timestamp_bracket() -> None:
    """Photo timestamps anchor the chronology — surfaced to the extractor
    so it can place beats on the morning/afternoon axis."""
    client = _client(_valid_extractor_json())

    extract_ledger(_hike_input(), _gpx_stats(), _photos(n=4), client=client)

    sent = client.complete.call_args.kwargs["prompt"]
    # First photo at 09:00, last at 12:00 (3 photos start at 09:00 + i hours).
    assert "2026-04-18T09:00" in sent
    assert "2026-04-18T12:00" in sent


def test_extract_ledger_falls_back_to_epoch_when_gpx_has_no_timestamps() -> None:
    """Manually-edited GPX files sometimes strip timing — the deterministic
    ``when`` field must still validate. Epoch is the sentinel; the season
    string surfaces 'unknown' so the writer sees the gap."""
    client = _client(_valid_extractor_json())
    stats = _gpx_stats(waypoint_time=None)

    ledger = extract_ledger(_hike_input(), stats, _photos(), client=client)

    assert ledger.when == datetime(1970, 1, 1, tzinfo=UTC)
    assert ledger.season == "unknown"


# ── extract_ledger retry / failure modes (mirrors generate_narrative) ────────


def test_extract_ledger_strips_markdown_fences() -> None:
    fenced = "```json\n" + _valid_extractor_json() + "\n```"
    client = _client(fenced)

    ledger = extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)

    assert ledger.people[0].name == "Mia"
    assert client.complete.call_count == 1


def test_extract_ledger_retries_on_unparseable_first_attempt() -> None:
    """One retry on a JSON-parse failure — same policy as the writer."""
    client = _client("here is my analysis: ...", _valid_extractor_json())

    ledger = extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)

    assert ledger.weather == "amazing weather"
    assert client.complete.call_count == 2


def test_extract_ledger_raises_after_two_unparseable_attempts() -> None:
    client = _client("prose one", "prose two")

    with pytest.raises(LedgerExtractionError, match="non-JSON output on both"):
        extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)
    assert client.complete.call_count == 2


def test_extract_ledger_raises_on_validation_error_without_retry() -> None:
    """Schema-validation failures are not retried — they're model bugs, not
    transients. Burning another paid call won't help."""
    bad = json.dumps({"people": [], "weather": "amazing"})  # missing chronology
    client = _client(bad, _valid_extractor_json())

    with pytest.raises(LedgerExtractionError, match="schema"):
        extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)
    assert client.complete.call_count == 1


def test_extract_ledger_translates_llm_response_error() -> None:
    client = _client(LLMResponseError("empty response"))

    with pytest.raises(LedgerExtractionError, match="Ledger LLM call failed"):
        extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)


def test_extract_ledger_translates_llm_retry_exhausted() -> None:
    client = _client(LLMRetryExhaustedError("rate-limited 3x"))

    with pytest.raises(LedgerExtractionError, match="Ledger LLM call failed"):
        extract_ledger(_hike_input(), _gpx_stats(), _photos(), client=client)


def test_extract_ledger_rejects_empty_photo_list() -> None:
    """Photo count is required for the writer's selection step; fail early."""
    client = _client(_valid_extractor_json())

    with pytest.raises(LedgerExtractionError, match="at least one photo"):
        extract_ledger(_hike_input(), _gpx_stats(), [], client=client)
