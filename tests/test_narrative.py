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

from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.llm.narrative import NarrativeGenerationError, generate_narrative
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


def _gpx_stats() -> GpxStats:
    return GpxStats(
        distance_km=6.2,
        elevation_gain_m=610,
        duration_min=165,
        start_elev_m=720.0,
        summit_elev_m=1330.0,
        waypoints=[
            Waypoint(lat=47.55, lon=11.78, ele_m=720.0, time=None),
            Waypoint(lat=47.56, lon=11.79, ele_m=1330.0, time=None),
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
        "schema_version": 2,
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
        "paragraphs": {
            "en": [
                "We left the trailhead at first light.",
                "By the saddle the cloud was thinning.",
                "Mia slept the whole climb, her cheek warm against the carrier.",
            ],
            "ru": [
                # noqa lines: "с" and "К" are genuine single-letter Russian
                # prepositions; ruff flags them as Cyrillic-Latin lookalikes
                # (RUF001), but they are correct Russian here.
                "Вышли на тропу с первыми лучами.",  # noqa: RUF001
                "К седловине облака начали редеть.",  # noqa: RUF001
                "Мия проспала весь подъём, прижавшись щекой к переноске.",
            ],
            "de": [
                "Bei erstem Licht brachen wir auf.",
                "Am Sattel begann die Wolke sich zu lichten.",
                "Mia schlief den ganzen Aufstieg, die Wange warm an der Trage.",
            ],
        },
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
    """Build a mocked client whose ``.complete`` yields each item in turn."""
    mock = MagicMock(spec=AnthropicClient)
    mock.complete.side_effect = list(responses)
    # ``cache_key`` reads ``client.model``; with spec=AnthropicClient that
    # would be a MagicMock and json.dumps would fail. Pin it to a string
    # so any test that does opt into the cache still works.
    mock.model = "claude-opus-4-7-test"
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

    out = generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

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

    out = generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

    assert out.title.en == "Above the fog line"
    # Fenced response parsed on the first attempt — no retry needed.
    assert client.complete.call_count == 1


def test_generate_narrative_strips_bare_triple_backtick_fence() -> None:
    fenced = "```\n" + _valid_response_json() + "\n```"
    client = _client(fenced)

    out = generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

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
        location="Tegernsee, Bavaria",
        **_NO_CACHE,
    )

    sent = client.complete.call_args.kwargs["prompt"]
    # Hike data is interpolated into the prompt.
    assert "Tegernsee, Bavaria" in sent
    assert "6.2" in sent  # distance_km
    assert "610" in sent  # elevation_gain_m
    assert "1330" in sent  # summit_elev_m
    assert "165" in sent  # duration_min
    assert "fog cleared" in sent
    # Photo count and zero-indexed upper bound.
    assert "Photos available: 8 (indexed 0-7)" in sent


def test_generate_narrative_uses_default_location_when_not_supplied() -> None:
    client = _client(_valid_response_json())

    generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

    sent = client.complete.call_args.kwargs["prompt"]
    assert "the trail" in sent


def test_generate_narrative_prefers_hike_input_location_name() -> None:
    """``HikeInput.location_name`` overrides the ``location`` kwarg fallback."""
    client = _client(_valid_response_json())
    hike = _hike_input().model_copy(update={"location_name": "Watzmann"})

    generate_narrative(
        hike, _gpx_stats(), _photos(), client=client, location="ignored", **_NO_CACHE
    )

    sent = client.complete.call_args.kwargs["prompt"]
    assert "Watzmann" in sent
    assert "ignored" not in sent


def test_generate_narrative_passes_system_prompt() -> None:
    client = _client(_valid_response_json())

    generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

    system = client.complete.call_args.kwargs["system"]
    assert "warm" in system.lower() or "memories" in system.lower()


# ── retry on JSON-parse failure ──────────────────────────────────────────────


def test_generate_narrative_retries_on_invalid_json_then_succeeds() -> None:
    prose = "Sure! Here's the memory you asked for: it was a beautiful day..."
    client = _client(prose, _valid_response_json())

    out = generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

    assert out.title.en == "Above the fog line"
    assert client.complete.call_count == 2


def test_generate_narrative_retry_appends_json_only_directive() -> None:
    client = _client("not json at all", _valid_response_json())

    generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

    first_prompt = client.complete.call_args_list[0].kwargs["prompt"]
    second_prompt = client.complete.call_args_list[1].kwargs["prompt"]
    assert second_prompt == first_prompt + USER_NARRATIVE_RETRY_SUFFIX


def test_generate_narrative_raises_after_two_invalid_json_attempts() -> None:
    client = _client("first prose", "second prose")

    with pytest.raises(NarrativeGenerationError, match="non-JSON output on both"):
        generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)
    assert client.complete.call_count == 2


def test_generate_narrative_treats_json_array_as_parse_failure() -> None:
    """Top-level JSON array is well-formed JSON but not a NarrativeOutput
    object — should trigger the retry path, not a validation error."""
    client = _client("[1, 2, 3]", _valid_response_json())

    out = generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)

    assert out.title.en == "Above the fog line"
    assert client.complete.call_count == 2


# ── validation failures (no retry per spec) ──────────────────────────────────


def test_generate_narrative_raises_on_validation_error() -> None:
    """JSON parses but is missing a required field — surface immediately,
    no retry (per Step 7 spec, retry is only for parse failures)."""
    incomplete = json.dumps({"title": {"en": "x", "ru": "y", "de": "z"}})  # missing required
    client = _client(incomplete)

    with pytest.raises(NarrativeGenerationError, match="schema"):
        generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)
    assert client.complete.call_count == 1


def test_generate_narrative_does_not_retry_on_validation_error() -> None:
    """Belt-and-braces version of the above: even if a 2nd response was
    queued, validation failure must not consume it."""
    incomplete = json.dumps({"title": {"en": "x", "ru": "y", "de": "z"}})
    second = _valid_response_json()
    client = _client(incomplete, second)

    with pytest.raises(NarrativeGenerationError):
        generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)
    assert client.complete.call_count == 1


# ── client-level errors ──────────────────────────────────────────────────────


def test_generate_narrative_translates_llm_response_error() -> None:
    client = _client(LLMResponseError("empty response"))

    with pytest.raises(NarrativeGenerationError, match="LLM call failed"):
        generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)


def test_generate_narrative_translates_llm_retry_exhausted() -> None:
    client = _client(LLMRetryExhaustedError("rate-limited 3x"))

    with pytest.raises(NarrativeGenerationError, match="LLM call failed"):
        generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)


def test_generate_narrative_does_not_retry_on_llm_error() -> None:
    """Client-level errors are not retried at this layer; the client owns
    its own retry policy. A second queued response must not be consumed."""
    client = _client(LLMResponseError("boom"), _valid_response_json())

    with pytest.raises(NarrativeGenerationError):
        generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)
    assert client.complete.call_count == 1


# ── input guards ─────────────────────────────────────────────────────────────


def test_generate_narrative_rejects_empty_photo_list() -> None:
    client = _client()  # should never be called

    with pytest.raises(NarrativeGenerationError, match="at least one photo"):
        generate_narrative(_hike_input(), _gpx_stats(), [], client=client, **_NO_CACHE)
    client.complete.assert_not_called()


def test_generate_narrative_photo_count_matches_photos() -> None:
    """``n_photos`` placeholder reflects the actual list length."""
    client = _client(_valid_response_json())

    generate_narrative(_hike_input(), _gpx_stats(), _photos(n=4), client=client, **_NO_CACHE)

    sent = client.complete.call_args.kwargs["prompt"]
    assert "Photos available: 4 (indexed 0-3)" in sent


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
    generate_narrative(_hike_input(), _gpx_stats(), _photos(), client=client, **_NO_CACHE)
    sent = client.complete.call_args.kwargs["prompt"]

    # Sanity check: no remaining {placeholder} tokens.
    import re

    leftover = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", sent)
    assert leftover == [], f"unfilled placeholders {leftover}; expected none of {expected}"
