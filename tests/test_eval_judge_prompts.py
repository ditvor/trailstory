"""Tests for ``tests.eval.judge_prompts``.

Mirrors the pattern in :mod:`tests.test_prompts`: we guard the contract
between the judge's prompt templates and the rest of the eval layer.

* the system prompt is a plain literal (no placeholders to fill),
* the user template's placeholders match what
  :mod:`tests.eval.judge` is expected to supply,
* the JSON skeleton embedded in the user template stays in sync with
  :class:`tests.eval.judge.JudgeScore`,
* a fully-formatted prompt has no leftover ``{...}`` placeholders.

When ``JudgeScore`` changes, the drift tests below will fail until
``USER_JUDGE_TEMPLATE`` is updated — that's intentional.
"""

from __future__ import annotations

import json
import re
from string import Formatter

import pytest

from tests.eval.judge import JudgeScore
from tests.eval.judge_prompts import (
    SYSTEM_JUDGE,
    USER_JUDGE_RETRY_SUFFIX,
    USER_JUDGE_TEMPLATE,
)

EXPECTED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "seed_text",
        "narrative_json",
    }
)


def _placeholders(template: str) -> set[str]:
    """Extract the set of named ``{field}`` placeholders from a format string."""
    return {name for _, name, _, _ in Formatter().parse(template) if name}


@pytest.fixture
def sample_fields() -> dict[str, object]:
    """Plausible values for every documented placeholder."""
    return {
        "seed_text": "The fog cleared just as we reached the ridge.",
        "narrative_json": json.dumps(
            {
                "title": {"en": "Above the fog line", "ru": "x", "de": "y"},
                "paragraphs": {"en": ["short stub paragraph"], "ru": ["x"], "de": ["y"]},
            },
            ensure_ascii=False,
            indent=2,
        ),
    }


# ── system prompt ────────────────────────────────────────────────────────────


def test_system_judge_is_non_empty_string() -> None:
    assert isinstance(SYSTEM_JUDGE, str)
    assert SYSTEM_JUDGE.strip()


def test_system_judge_has_no_placeholders() -> None:
    """The system prompt is sent verbatim — no caller-supplied substitutions."""
    assert _placeholders(SYSTEM_JUDGE) == set()


def test_system_judge_sets_persona_and_output_contract() -> None:
    """Smoke test: the prompt names its role and demands JSON output.

    Loose substring checks — exact wording is allowed to evolve.
    """
    text = SYSTEM_JUDGE.lower()
    assert "rubric" in text
    assert "json" in text
    # The judge needs to read both languages to score russian_fidelity.
    assert "russian" in text


# ── user template — placeholders ─────────────────────────────────────────────


def test_user_template_placeholders_match_expected() -> None:
    """The set of placeholders in the template equals the documented set.

    Catches typos in either direction: a missing placeholder (caller forgets
    to supply it) and an unexpected new placeholder (caller silently drops a
    field, ``.format`` raises KeyError at runtime).
    """
    assert _placeholders(USER_JUDGE_TEMPLATE) == EXPECTED_PLACEHOLDERS


def test_user_template_formats_with_documented_keys(
    sample_fields: dict[str, object],
) -> None:
    rendered = USER_JUDGE_TEMPLATE.format(**sample_fields)
    assert isinstance(rendered, str)
    assert rendered.strip()


def test_user_template_format_propagates_values(
    sample_fields: dict[str, object],
) -> None:
    rendered = USER_JUDGE_TEMPLATE.format(**sample_fields)
    assert "fog cleared" in rendered
    assert "Above the fog line" in rendered  # comes via narrative_json


def test_user_template_raises_on_missing_field(
    sample_fields: dict[str, object],
) -> None:
    incomplete = dict(sample_fields)
    incomplete.pop("seed_text")
    with pytest.raises(KeyError):
        USER_JUDGE_TEMPLATE.format(**incomplete)


def test_formatted_prompt_has_no_leftover_placeholders(
    sample_fields: dict[str, object],
) -> None:
    """After ``.format()`` no ``{name}`` token should remain.

    Excludes JSON braces, which are correctly escaped as ``{{`` / ``}}``.
    """
    rendered = USER_JUDGE_TEMPLATE.format(**sample_fields)
    leftover = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", rendered)
    assert leftover == []


# ── user template — JSON skeleton drift ──────────────────────────────────────


def test_user_template_mentions_every_judge_score_field() -> None:
    """Embedded skeleton in the prompt must list every ``JudgeScore`` field.

    If a new axis is added to ``JudgeScore`` and someone forgets to update
    the prompt, this fails — fulfilling the same drift-guard convention as
    the writer's ``test_prompts.py``.
    """
    missing = [name for name in JudgeScore.model_fields if f'"{name}"' not in USER_JUDGE_TEMPLATE]
    assert missing == [], f"JudgeScore fields absent from prompt: {missing}"


def test_user_template_embedded_json_skeleton_is_valid_json(
    sample_fields: dict[str, object],
) -> None:
    """The JSON example block in the rendered prompt must parse as JSON.

    We extract the first balanced ``{...}`` block after the ``Output only
    JSON`` marker and feed it to ``json.loads``. Mirrors
    :func:`tests.test_prompts.test_user_template_embedded_json_skeleton_is_valid_json`.
    """
    rendered = USER_JUDGE_TEMPLATE.format(**sample_fields)
    start = rendered.index("{", rendered.index("Output only JSON"))
    depth = 0
    end = -1
    for i, ch in enumerate(rendered[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end > start, "could not locate closing brace of JSON skeleton"
    skeleton = rendered[start : end + 1]

    parsed = json.loads(skeleton)
    # Every top-level key in the skeleton must be a JudgeScore field.
    assert set(parsed.keys()) == set(JudgeScore.model_fields)


# ── instruction content ──────────────────────────────────────────────────────


def test_user_template_describes_each_axis() -> None:
    """The rubric definition for every JudgeScore axis must be present.

    Without per-axis guidance the model invents its own scoring criteria
    and the deltas are meaningless.
    """
    text = USER_JUDGE_TEMPLATE.lower()
    for axis in JudgeScore.model_fields:
        if axis == "notes":
            continue
        assert axis in text, f"axis {axis!r} not described in judge user prompt"


def test_user_template_states_zero_to_five_scale() -> None:
    """Loose smoke test: the prompt names the 0-5 scale somewhere."""
    assert "0-5" in USER_JUDGE_TEMPLATE


# ── retry suffix ─────────────────────────────────────────────────────────────


def test_retry_suffix_is_non_empty_string() -> None:
    assert isinstance(USER_JUDGE_RETRY_SUFFIX, str)
    assert USER_JUDGE_RETRY_SUFFIX.strip()


def test_retry_suffix_demands_json_only() -> None:
    """The directive used by ``judge.py`` on JSON-parse-failure retries."""
    text = USER_JUDGE_RETRY_SUFFIX.lower()
    assert "json" in text
    assert "no prose" in text


def test_retry_suffix_has_no_placeholders() -> None:
    """Suffix is appended verbatim — no caller-supplied substitutions."""
    assert _placeholders(USER_JUDGE_RETRY_SUFFIX) == set()
