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

from tests.conftest import paragraphs_from_strings
from tests.eval.judge import (
    DEFAULT_JUDGE_MODEL,
    ClaimVerdict,
    FaithfulnessVerdict,
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
        schema_version=3,
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
        paragraphs=paragraphs_from_strings(
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


# ── faithfulness axis (ADR-007) ──────────────────────────────────────────────
#
# The faithfulness axis is a derived ``@computed_field`` on JudgeScore.
# The judge produces ``claim_verdicts``; Python does the arithmetic.
# Tests cover both halves: the wiring (judge response → ClaimVerdict
# list) and the math (verdicts → derived float).


def _verdicts(
    n_supported: int = 0, n_inferred: int = 0, n_unsupported: int = 0
) -> list[dict[str, str]]:
    """Build a claim_verdicts list for ``_valid_judge_dict`` overrides."""
    out: list[dict[str, str]] = []
    for i in range(n_supported):
        out.append({"claim": f"supported {i}", "verdict": "SUPPORTED", "quote": "source"})
    for i in range(n_inferred):
        out.append({"claim": f"inferred {i}", "verdict": "INFERRED", "quote": "from photo"})
    for i in range(n_unsupported):
        out.append({"claim": f"unsupported {i}", "verdict": "UNSUPPORTED", "quote": ""})
    return out


def test_claim_verdict_validates_verdict_enum() -> None:
    """The verdict field must be one of the three documented values."""
    valid = ClaimVerdict(claim="Danny was there", verdict=FaithfulnessVerdict.SUPPORTED)
    assert valid.verdict == FaithfulnessVerdict.SUPPORTED
    assert valid.quote == ""  # default

    with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
        ClaimVerdict.model_validate({"claim": "x", "verdict": "MAYBE", "quote": ""})


def test_faithfulness_empty_verdicts_returns_zero() -> None:
    """Missing verdicts and entirely-unsupported are indistinguishable —
    intentional, so pre-faithfulness goldens regress as expected."""
    score = JudgeScore.model_validate(_valid_judge_dict())
    assert score.claim_verdicts == []
    assert score.faithfulness == 0.0


def test_faithfulness_all_supported_scores_full() -> None:
    score = JudgeScore.model_validate(_valid_judge_dict(claim_verdicts=_verdicts(n_supported=10)))
    assert score.faithfulness == 5.0


def test_faithfulness_all_unsupported_scores_zero() -> None:
    score = JudgeScore.model_validate(_valid_judge_dict(claim_verdicts=_verdicts(n_unsupported=10)))
    assert score.faithfulness == 0.0


def test_faithfulness_inferred_weighted_half() -> None:
    """4 verdicts: 2 supported, 1 inferred, 1 unsupported.
    score = (2·1.0 + 1·0.5 + 1·0.0) / 4 · 5 = 2.5 / 4 · 5 = 3.125.

    Rounded to 2 decimals via Python's banker's rounding → 3.12. The
    rounding noise sits well below the 1.0 regression threshold; the
    score is for human display, not arithmetic, so 2dp is fine.
    """
    score = JudgeScore.model_validate(
        _valid_judge_dict(claim_verdicts=_verdicts(n_supported=2, n_inferred=1, n_unsupported=1))
    )
    assert score.faithfulness == pytest.approx(3.125, abs=0.01)


def test_faithfulness_appears_in_model_dump() -> None:
    """Goldens are persisted via ``model_dump(mode='json')`` — the
    computed field must show up there so a human reading the golden file
    can see the derived score without recomputing."""
    score = JudgeScore.model_validate(
        _valid_judge_dict(claim_verdicts=_verdicts(n_supported=3, n_unsupported=1))
    )
    dumped = score.model_dump(mode="json")
    assert "faithfulness" in dumped
    assert dumped["faithfulness"] == pytest.approx(3.75)
    # And claim_verdicts is preserved verbatim for human auditing.
    assert len(dumped["claim_verdicts"]) == 4


def test_faithfulness_ignored_on_explicit_input() -> None:
    """An explicit ``faithfulness`` key in the input dict is silently
    dropped — the computed_field always wins. This protects against a
    judge hallucinating a contradictory score."""
    data = _valid_judge_dict(
        claim_verdicts=_verdicts(n_supported=10),  # → 5.0
        faithfulness=0.0,  # noise from a confused judge
    )
    score = JudgeScore.model_validate(data)
    assert score.faithfulness == 5.0


def test_judge_narrative_accepts_claim_verdicts_in_response() -> None:
    """End-to-end: the judge returns claim_verdicts and the score round-trips."""
    response = _valid_judge_json(
        claim_verdicts=_verdicts(n_supported=3, n_inferred=2, n_unsupported=1)
    )
    client = _client(response)

    score = judge_narrative(_narrative(), _hike_input(), client=client)

    assert len(score.claim_verdicts) == 6
    assert sum(1 for v in score.claim_verdicts if v.verdict == FaithfulnessVerdict.SUPPORTED) == 3
    # score = (3·1.0 + 2·0.5 + 1·0.0) / 6 · 5 = 4.0 / 6 · 5 ≈ 3.33
    assert score.faithfulness == pytest.approx(3.33, abs=0.01)


def test_judge_narrative_legacy_golden_format_validates_with_default_verdicts() -> None:
    """Pre-ADR-007 goldens have no ``claim_verdicts`` key; they must
    still validate (default empty list) so the regression gate doesn't
    fail-hard before goldens get refreshed."""
    legacy = json.dumps(
        {
            "warmth": 4.5,
            "narrative_arc": 4.0,
            "russian_fidelity": 4.5,
            "photo_selection_plausibility": 3.5,
            "notes": "old golden written before claim_verdicts existed.",
        }
    )
    client = _client(legacy)

    score = judge_narrative(_narrative(), _hike_input(), client=client)

    assert score.claim_verdicts == []
    assert score.faithfulness == 0.0
    assert score.warmth == pytest.approx(4.5)


def test_judge_prompt_describes_faithfulness_extraction() -> None:
    """The prompt must explain what a claim is and how to label it,
    otherwise the judge invents its own taxonomy and verdicts are noise."""
    from tests.eval.judge_prompts import USER_JUDGE_TEMPLATE

    text = USER_JUDGE_TEMPLATE
    assert "claim_verdicts" in text
    assert "SUPPORTED" in text
    assert "INFERRED" in text
    assert "UNSUPPORTED" in text
