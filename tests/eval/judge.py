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
from json import JSONDecodeError
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class JudgeScore(BaseModel):
    """Numeric scores plus free-form notes returned by the LLM judge.

    Each axis is a float on the 0-5 scale defined in the user prompt.
    Pydantic's ``ge=0, le=5`` bounds keep an out-of-range hallucination
    out of the downstream golden-delta math. ``notes`` carries the
    judge's justification so a human inspecting a regression can see
    *why* an axis dropped, not just that it did.
    """

    model_config = ConfigDict(frozen=True)

    warmth: float = Field(ge=0, le=5)
    narrative_arc: float = Field(ge=0, le=5)
    russian_fidelity: float = Field(ge=0, le=5)
    photo_selection_plausibility: float = Field(ge=0, le=5)
    notes: str


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
