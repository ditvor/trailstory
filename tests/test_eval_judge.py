"""Tests for ``tests.eval.judge``.

Every test injects a ``MagicMock(spec=AnthropicClient)`` — no real network
calls, per CLAUDE.md ("Always mocks the Anthropic client. Never calls the
real API."). The paid runner that actually exercises the judge against
real model output lives at ``tests/eval/run.py`` behind ``make eval-live``
and is intentionally not part of CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.eval.judge import (
    DEFAULT_JUDGE_MODEL,
    JudgeError,
    JudgeScore,
    judge_narrative,
)
from tests.eval.judge_prompts import USER_JUDGE_RETRY_SUFFIX
from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.models import (
    HikeInput,
    LocalizedParagraphs,
    LocalizedString,
    NarrativeOutput,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _hike_input() -> HikeInput:
    return HikeInput(
        gpx_path=Path("/tmp/hike.gpx"),
        photos_dir=Path("/tmp/photos"),
        seed_text="The fog cleared just as we reached the ridge.",
    )


def _narrative() -> NarrativeOutput:
    return NarrativeOutput(
        schema_version=2,
        title=LocalizedString(
            en="Above the fog line",
            ru="Над линией тумана",
            de="Über der Nebelgrenze",
        ),
        subtitle=LocalizedString(
            en="A morning above the cloud sea",
            ru="Утро над морем облаков",
            de="Ein Morgen über dem Wolkenmeer",
        ),
        paragraphs=LocalizedParagraphs(
            en=[
                "We left the trailhead at first light.",
                "By the saddle the cloud was thinning.",
                "Mia slept the whole climb.",
            ],
            ru=[
                "Вышли на тропу с первыми лучами.",  # noqa: RUF001
                "К седловине облака начали редеть.",  # noqa: RUF001
                "Мия проспала весь подъём.",
            ],
            de=[
                "Bei erstem Licht brachen wir auf.",
                "Am Sattel begann die Wolke sich zu lichten.",
                "Mia schlief den ganzen Aufstieg.",
            ],
        ),
        pull_quote=LocalizedString(
            en="The fog cleared just as we reached the ridge.",
            ru="Туман рассеялся как раз когда мы вышли на хребет.",
            de="Der Nebel lichtete sich, gerade als wir den Grat erreichten.",
        ),
        milestone=LocalizedString(
            en="First mountain hike",
            ru="Первый горный поход",
            de="Erste Bergwanderung",
        ),
        selected_photo_indices=[0, 2, 4, 6, 8, 10],
    )


def _valid_judge_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "warmth": 4.5,
        "narrative_arc": 4.0,
        "russian_fidelity": 4.5,
        "photo_selection_plausibility": 3.5,
        "notes": "Specific sensory detail, even paragraph weight, natural Russian.",
    }
    base.update(overrides)
    return base


def _valid_judge_json(**overrides: object) -> str:
    return json.dumps(_valid_judge_dict(**overrides))


def _client(*responses: str | Exception) -> MagicMock:
    """Build a mocked client whose ``.complete`` yields each item in turn."""
    mock = MagicMock(spec=AnthropicClient)
    mock.complete.side_effect = list(responses)
    mock.model = DEFAULT_JUDGE_MODEL
    return mock


# ── happy path ───────────────────────────────────────────────────────────────


def test_judge_narrative_happy_path() -> None:
    client = _client(_valid_judge_json())

    score = judge_narrative(_narrative(), _hike_input(), client=client)

    assert isinstance(score, JudgeScore)
    assert score.warmth == pytest.approx(4.5)
    assert score.narrative_arc == pytest.approx(4.0)
    assert score.russian_fidelity == pytest.approx(4.5)
    assert score.photo_selection_plausibility == pytest.approx(3.5)
    assert "sensory" in score.notes
    assert client.complete.call_count == 1


def test_judge_narrative_strips_markdown_fences() -> None:
    """Models often wrap JSON in ```json``` despite being told not to."""
    fenced = "```json\n" + _valid_judge_json() + "\n```"
    client = _client(fenced)

    score = judge_narrative(_narrative(), _hike_input(), client=client)

    assert score.warmth == pytest.approx(4.5)
    assert client.complete.call_count == 1


def test_judge_narrative_strips_bare_triple_backtick_fence() -> None:
    fenced = "```\n" + _valid_judge_json() + "\n```"
    client = _client(fenced)

    score = judge_narrative(_narrative(), _hike_input(), client=client)

    assert score.narrative_arc == pytest.approx(4.0)
    assert client.complete.call_count == 1


# ── prompt wiring ────────────────────────────────────────────────────────────


def test_judge_narrative_passes_hike_context_to_prompt() -> None:
    client = _client(_valid_judge_json())

    judge_narrative(_narrative(), _hike_input(), client=client)

    sent = client.complete.call_args.kwargs["prompt"]
    assert "fog cleared" in sent  # seed_text


def test_judge_narrative_embeds_full_narrative_json_in_prompt() -> None:
    client = _client(_valid_judge_json())

    judge_narrative(_narrative(), _hike_input(), client=client)

    sent = client.complete.call_args.kwargs["prompt"]
    # A representative slice of the narrative — every layer (title,
    # paragraph, indices) shows up in the rendered prompt.
    assert "Above the fog line" in sent
    assert "Над линией тумана" in sent
    assert "Туман рассеялся" in sent
    assert "First mountain hike" in sent


def test_judge_narrative_passes_judge_system_prompt() -> None:
    client = _client(_valid_judge_json())

    judge_narrative(_narrative(), _hike_input(), client=client)

    system = client.complete.call_args.kwargs["system"]
    text = system.lower()
    assert "rubric" in text
    assert "json" in text


# ── retry on JSON-parse failure ──────────────────────────────────────────────


def test_judge_narrative_retries_on_invalid_json_then_succeeds() -> None:
    prose = "Here is my judgement: this narrative was lovely..."
    client = _client(prose, _valid_judge_json())

    score = judge_narrative(_narrative(), _hike_input(), client=client)

    assert score.warmth == pytest.approx(4.5)
    assert client.complete.call_count == 2


def test_judge_narrative_retry_appends_json_only_directive() -> None:
    client = _client("not json at all", _valid_judge_json())

    judge_narrative(_narrative(), _hike_input(), client=client)

    first_prompt = client.complete.call_args_list[0].kwargs["prompt"]
    second_prompt = client.complete.call_args_list[1].kwargs["prompt"]
    assert second_prompt == first_prompt + USER_JUDGE_RETRY_SUFFIX


def test_judge_narrative_raises_after_two_invalid_json_attempts() -> None:
    client = _client("first prose", "second prose")

    with pytest.raises(JudgeError, match="non-JSON output on both"):
        judge_narrative(_narrative(), _hike_input(), client=client)
    assert client.complete.call_count == 2


def test_judge_narrative_treats_json_array_as_parse_failure() -> None:
    """Top-level JSON array is well-formed JSON but not a JudgeScore
    object — should trigger the retry path, not a validation error."""
    client = _client("[1, 2, 3]", _valid_judge_json())

    score = judge_narrative(_narrative(), _hike_input(), client=client)

    assert score.warmth == pytest.approx(4.5)
    assert client.complete.call_count == 2


# ── validation failures (no retry per spec) ──────────────────────────────────


def test_judge_narrative_raises_on_validation_error() -> None:
    """JSON parses but is missing a required axis — surface immediately."""
    incomplete = json.dumps({"warmth": 3.0})
    client = _client(incomplete)

    with pytest.raises(JudgeError, match="schema"):
        judge_narrative(_narrative(), _hike_input(), client=client)
    assert client.complete.call_count == 1


def test_judge_narrative_raises_on_out_of_range_axis() -> None:
    """Pydantic ``ge=0, le=5`` bounds reject hallucinated extreme scores."""
    client = _client(_valid_judge_json(warmth=9.9))

    with pytest.raises(JudgeError, match="schema"):
        judge_narrative(_narrative(), _hike_input(), client=client)


def test_judge_narrative_does_not_retry_on_validation_error() -> None:
    """Belt-and-braces: even if a 2nd response was queued, validation
    failure must not consume it."""
    incomplete = json.dumps({"warmth": 3.0})
    client = _client(incomplete, _valid_judge_json())

    with pytest.raises(JudgeError):
        judge_narrative(_narrative(), _hike_input(), client=client)
    assert client.complete.call_count == 1


# ── client-level errors ──────────────────────────────────────────────────────


def test_judge_narrative_translates_llm_response_error() -> None:
    client = _client(LLMResponseError("empty response"))

    with pytest.raises(JudgeError, match="Judge LLM call failed"):
        judge_narrative(_narrative(), _hike_input(), client=client)


def test_judge_narrative_translates_llm_retry_exhausted() -> None:
    client = _client(LLMRetryExhaustedError("rate-limited 3x"))

    with pytest.raises(JudgeError, match="Judge LLM call failed"):
        judge_narrative(_narrative(), _hike_input(), client=client)


def test_judge_narrative_does_not_retry_on_llm_error() -> None:
    """Client-level errors are not retried at this layer; the client owns
    its own retry policy. A second queued response must not be consumed."""
    client = _client(LLMResponseError("boom"), _valid_judge_json())

    with pytest.raises(JudgeError):
        judge_narrative(_narrative(), _hike_input(), client=client)
    assert client.complete.call_count == 1
