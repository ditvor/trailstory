"""Paid LLM-as-judge layer for narrative-quality evaluation.

Companion to the always-on programmatic rubric in :mod:`tests.eval.rubric`.
The rubric catches structural and gross translation failures for free; the
judge here scores taste-level dimensions (warmth, arc, Russian fidelity,
photo-selection plausibility) using a paid Anthropic call. The split is
intentional — see "Paid judge layer" in
``docs/adr/003-narrative-eval-suite.md`` and the original ADR rationale.

Why a different model than the writer?

The narrative pipeline defaults to ``claude-opus-4-7``. Asking the same
model family to score its own output inflates scores: the judge agrees
with its own stylistic choices. We score with ``claude-sonnet-4-6`` by
default — cheaper, and a different perspective. The judge model is
configurable via the ``EVAL_JUDGE_MODEL`` env var; the runner in
:mod:`tests.eval.run` constructs the client with the configured model
and passes it in, so this module does not read the env directly.

This module never calls the SDK directly: it goes through
:class:`trailstory.llm.client.AnthropicClient` like the rest of the
codebase. Errors are funnelled through :class:`JudgeError`, the only
judge-side exception type that escapes ``tests/eval/``.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from json import JSONDecodeError
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from tests.eval.judge_prompts import (
    SYSTEM_JUDGE,
    USER_JUDGE_RETRY_SUFFIX,
    USER_JUDGE_TEMPLATE,
)
from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.models import HikeInput, NarrativeOutput

logger = logging.getLogger(__name__)

# Default judge model. Different family from the writer (Opus 4.7) on
# purpose — see module docstring. Override with the ``EVAL_JUDGE_MODEL``
# env var; the runner reads that and builds a client with the right model.
DEFAULT_JUDGE_MODEL: str = "claude-sonnet-4-6"


class JudgeError(Exception):
    """Terminal failure of the judge pipeline.

    Raised when the LLM call ultimately fails, when both JSON-parse
    attempts return non-JSON, or when the parsed JSON does not validate
    against :class:`JudgeScore`.
    """


class FaithfulnessVerdict(StrEnum):
    """Per-claim faithfulness label assigned by the judge.

    The values are also the JSON tokens the judge must emit, so the enum
    and the prompt stay in lock-step. Adding a verdict here without
    updating the rubric paragraph in
    :data:`tests.eval.judge_prompts.USER_JUDGE_TEMPLATE` is a bug — the
    drift tests in ``tests/test_eval_judge_prompts.py`` will flag it.
    """

    SUPPORTED = "SUPPORTED"
    INFERRED = "INFERRED"
    UNSUPPORTED = "UNSUPPORTED"


class ClaimVerdict(BaseModel):
    """One concrete factual claim extracted from the narrative.

    The judge walks the English narrative and produces one of these for
    each concrete claim (named people, named objects, specific actions,
    weather/season/scene details). Prose, metaphor and rhythm are not
    claims — the judge is told to skip them.

    ``quote`` carries the source span the verdict was grounded in. For
    ``SUPPORTED`` it is the exact phrase from the seed text. For
    ``INFERRED`` it is whatever the judge inferred from (e.g. a photo
    description, the GPX date). For ``UNSUPPORTED`` it is empty —
    nothing to quote.
    """

    model_config = ConfigDict(frozen=True)

    claim: str
    verdict: FaithfulnessVerdict
    quote: str = ""


# Per-verdict weights folded into the derived faithfulness score. SUPPORTED
# claims count fully, INFERRED claims count half, UNSUPPORTED claims count
# zero. Tuned to penalise outright fabrication while still rewarding
# reasonable inference from photos/GPX that is plausibly grounded.
_VERDICT_WEIGHTS: dict[FaithfulnessVerdict, float] = {
    FaithfulnessVerdict.SUPPORTED: 1.0,
    FaithfulnessVerdict.INFERRED: 0.5,
    FaithfulnessVerdict.UNSUPPORTED: 0.0,
}


class JudgeScore(BaseModel):
    """Numeric scores plus free-form notes returned by the LLM judge.

    Each LLM-scored axis is a float on the 0-5 scale defined in the user
    prompt. Pydantic's ``ge=0, le=5`` bounds keep an out-of-range
    hallucination out of the downstream golden-delta math. ``notes``
    carries the judge's justification so a human inspecting a regression
    can see *why* an axis dropped, not just that it did.

    :attr:`faithfulness` is computed in Python from
    :attr:`claim_verdicts` — the LLM is unreliable at arithmetic, so the
    judge labels claims and the score derives deterministically. See
    ADR-007.
    """

    model_config = ConfigDict(frozen=True)

    warmth: float = Field(ge=0, le=5)
    narrative_arc: float = Field(ge=0, le=5)
    russian_fidelity: float = Field(ge=0, le=5)
    photo_selection_plausibility: float = Field(ge=0, le=5)
    claim_verdicts: list[ClaimVerdict] = Field(default_factory=list)
    notes: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def faithfulness(self) -> float:
        """Derived faithfulness score on the same 0-5 scale as the other axes.

        Formula:
            (Σ weight(verdict) for each ClaimVerdict) / n_claims · 5

        An empty :attr:`claim_verdicts` list returns ``0.0`` — a missing
        verdict list and an entirely-unsupported narrative are
        indistinguishable to the regression gate, which is the right
        default ("if we have no evidence of faithfulness, assume the
        worst"). Pre-faithfulness goldens (no ``claim_verdicts`` key) get
        this default at validation time; first ``make eval-live`` refresh
        populates real verdicts.
        """
        if not self.claim_verdicts:
            return 0.0
        total = sum(_VERDICT_WEIGHTS[v.verdict] for v in self.claim_verdicts)
        return round(total / len(self.claim_verdicts) * 5.0, 2)


def judge_narrative(
    narrative: NarrativeOutput,
    hike_input: HikeInput,
    *,
    client: AnthropicClient,
) -> JudgeScore:
    """Score a narrative on the four taste-level rubric axes.

    Args:
        narrative: The narrative to score (already validated by the
            writer pipeline).
        hike_input: Source ``HikeInput``; the seed text is interpolated
            into the user prompt as context.
        client: Anthropic client wrapper. Caller is responsible for
            constructing this with the configured judge model
            (see :data:`DEFAULT_JUDGE_MODEL` and ``EVAL_JUDGE_MODEL``).

    Returns:
        Validated :class:`JudgeScore`.

    Raises:
        JudgeError: LLM call failed, model returned non-JSON twice in a
            row, or the parsed JSON did not validate against
            ``JudgeScore``.
    """
    narrative_json = json.dumps(
        narrative.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    base_prompt = USER_JUDGE_TEMPLATE.format(
        seed_text=hike_input.seed_text,
        narrative_json=narrative_json,
    )

    parsed = _call_and_parse(client, base_prompt)
    if parsed is None:
        logger.warning("judge response did not parse as JSON; retrying with explicit directive")
        retry_prompt = base_prompt + USER_JUDGE_RETRY_SUFFIX
        parsed = _call_and_parse(client, retry_prompt)
        if parsed is None:
            raise JudgeError("Judge model returned non-JSON output on both attempts.")

    try:
        return JudgeScore.model_validate(parsed)
    except ValidationError as exc:
        raise JudgeError(f"Judge JSON did not match JudgeScore schema: {exc}") from exc


# -- internal helpers ---------------------------------------------------------


def _call_and_parse(client: AnthropicClient, prompt: str) -> dict[str, Any] | None:
    """Call the judge once and try to parse the response as a JSON object.

    Returns the parsed ``dict`` on success, or ``None`` if the response
    is not a JSON object (parse failure, JSON array, JSON literal, etc.)
    so the caller can decide whether to retry. Client-level errors are
    funnelled into :class:`JudgeError` immediately because retrying them
    would duplicate the client's own retry policy.
    """
    try:
        raw = client.complete(prompt=prompt, system=SYSTEM_JUDGE)
    except (LLMResponseError, LLMRetryExhaustedError) as exc:
        raise JudgeError(f"Judge LLM call failed: {exc}") from exc

    cleaned = _strip_code_fences(raw)
    try:
        result = json.loads(cleaned)
    except JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _strip_code_fences(text: str) -> str:
    """Remove a leading / trailing markdown code fence if present.

    Tolerates the common case where the model wraps its JSON in
    `````json`` ... ``````` despite being told not to. Pure prose
    responses pass through unchanged and are caught downstream by
    ``json.loads``. Same logic as in
    :mod:`trailstory.llm.narrative` — kept duplicated to keep the eval
    layer independent of the writer's internal helpers.
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].rstrip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
