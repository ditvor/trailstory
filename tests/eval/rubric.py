"""Programmatic rubric for narrative-quality regression tests.

Each check inspects a ``NarrativeOutput`` (plus ``n_photos`` for the index
range check) and returns a single :class:`RubricResult`. The runner in
``tests.eval.run`` aggregates these results into a table and exits
non-zero if any failed.

The rubric is deliberately mechanical: no LLM calls, no judgment calls.
That keeps it free, fast, deterministic, and runnable in ``make ci``. A
separate paid LLM-judge layer for taste-level quality lives behind the
``feat/eval-judge`` gate (see ``docs/adr/003-narrative-eval-suite.md``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from trailstory.models import NarrativeOutput

# Cyrillic block (U+0400-U+04FF) plus Cyrillic Supplement (U+0500-U+052F).
# Spelled as Unicode escapes so the range is unambiguous in any terminal.
_CYRILLIC_RE: re.Pattern[str] = re.compile("[\u0400-\u052f]")

# Word tokens for overlap and ratio counting: any maximal run of letters or
# digits, ignoring underscores and punctuation. ``\W`` is locale-aware under
# ``re.UNICODE``, so this captures both Latin and Cyrillic words.
_WORD_RE: re.Pattern[str] = re.compile(r"[^\W_]+", flags=re.UNICODE)


@dataclass(frozen=True)
class RubricResult:
    """Outcome of a single rubric check."""

    name: str
    passed: bool
    detail: str


def schema_validates(narrative: NarrativeOutput) -> RubricResult:
    """Round-trip the narrative through Pydantic validation.

    Defensive: ``generate_narrative`` already validates before returning,
    so a fresh ``NarrativeOutput`` always passes. The check still runs
    inside the rubric so the table has a row recording that the model's
    JSON output validated, and so the unit-test suite exercises this
    invariant independently of the LLM pipeline.
    """
    name = "schema_validates"
    try:
        NarrativeOutput.model_validate(narrative.model_dump())
    except ValidationError as exc:
        return RubricResult(name, False, str(exc))
    return RubricResult(name, True, "ok")


def paragraph_count_3_to_5_each_lang(narrative: NarrativeOutput) -> RubricResult:
    """Both EN and RU paragraph lists must contain 3 to 5 entries."""
    name = "paragraph_count_3_to_5_each_lang"
    en = len(narrative.paragraphs_en)
    ru = len(narrative.paragraphs_ru)
    if 3 <= en <= 5 and 3 <= ru <= 5:
        return RubricResult(name, True, f"en={en}, ru={ru}")
    return RubricResult(name, False, f"en={en}, ru={ru} (expected 3-5 each)")


def russian_actually_cyrillic(narrative: NarrativeOutput) -> RubricResult:
    """RU paragraphs: each has ≥1 Cyrillic char and no run of >5 ASCII-letter words.

    The Cyrillic check rejects whole-paragraph translation failures. The
    ASCII-run check catches the common subtler failure: the model
    translates most of a paragraph but drops back into English for a
    sentence or two.
    """
    name = "russian_actually_cyrillic"
    for i, para in enumerate(narrative.paragraphs_ru):
        if not _CYRILLIC_RE.search(para):
            return RubricResult(name, False, f"paragraphs_ru[{i}] has no Cyrillic char")
        run = _max_ascii_letter_run(para)
        if run > 5:
            return RubricResult(
                name,
                False,
                f"paragraphs_ru[{i}] contains a run of {run} consecutive ASCII-letter words",
            )
    return RubricResult(name, True, "ok")


def word_count_ratio_en_ru_in_0_7_to_1_4(narrative: NarrativeOutput) -> RubricResult:
    """Total RU words divided by total EN words must be in [0.7, 1.4]."""
    name = "word_count_ratio_en_ru_in_0_7_to_1_4"
    en_words = sum(len(_words(p)) for p in narrative.paragraphs_en)
    ru_words = sum(len(_words(p)) for p in narrative.paragraphs_ru)
    if en_words == 0:
        return RubricResult(name, False, "EN paragraphs have no words")
    ratio = ru_words / en_words
    detail = f"ratio={ratio:.2f} (en={en_words}, ru={ru_words})"
    if 0.7 <= ratio <= 1.4:
        return RubricResult(name, True, detail)
    return RubricResult(name, False, f"{detail}; expected 0.7-1.4")


def title_under_60_chars(narrative: NarrativeOutput) -> RubricResult:
    """Both titles must be under 60 characters."""
    return _length_check(
        "title_under_60_chars",
        60,
        ("title_en", narrative.title_en),
        ("title_ru", narrative.title_ru),
    )


def subtitle_under_90_chars(narrative: NarrativeOutput) -> RubricResult:
    """Both subtitles must be under 90 characters."""
    return _length_check(
        "subtitle_under_90_chars",
        90,
        ("subtitle_en", narrative.subtitle_en),
        ("subtitle_ru", narrative.subtitle_ru),
    )


def milestone_under_30_chars(narrative: NarrativeOutput) -> RubricResult:
    """Both milestone tags must be under 30 characters."""
    return _length_check(
        "milestone_under_30_chars",
        30,
        ("milestone_en", narrative.milestone_en),
        ("milestone_ru", narrative.milestone_ru),
    )


def indices_valid(narrative: NarrativeOutput, n_photos: int) -> RubricResult:
    """``selected_photo_indices``: 6-8 entries, all in ``[0, n_photos)``, no dupes."""
    name = "indices_valid"
    indices = narrative.selected_photo_indices
    n = len(indices)
    if not 6 <= n <= 8:
        return RubricResult(name, False, f"got {n} indices; expected 6-8")
    out_of_range = [i for i in indices if not 0 <= i < n_photos]
    if out_of_range:
        return RubricResult(
            name,
            False,
            f"out-of-range indices {out_of_range} (n_photos={n_photos})",
        )
    if len(set(indices)) != n:
        return RubricResult(name, False, f"duplicate indices in {indices}")
    return RubricResult(name, True, f"{n} indices, all valid")


def pull_quote_drawn_from_body(narrative: NarrativeOutput) -> RubricResult:
    """``pull_quote_en`` shares ≥60% of its words with at least one EN paragraph.

    Set-based overlap: ``|quote ∩ paragraph| / |quote|`` taken as the max
    over all paragraphs. This catches pull-quotes that are made up wholesale
    rather than distilled from the body, while tolerating light paraphrase.
    """
    name = "pull_quote_drawn_from_body"
    quote_words = set(_words(narrative.pull_quote_en))
    if not quote_words:
        return RubricResult(name, False, "pull_quote_en has no words")
    best = 0.0
    for para in narrative.paragraphs_en:
        para_words = set(_words(para))
        overlap = len(quote_words & para_words) / len(quote_words)
        if overlap > best:
            best = overlap
    if best >= 0.60:
        return RubricResult(name, True, f"max overlap = {best:.2f}")
    return RubricResult(name, False, f"max overlap = {best:.2f}; expected ≥ 0.60")


def apply_rubric(narrative: NarrativeOutput, n_photos: int) -> list[RubricResult]:
    """Run every rubric check and return the results in display order."""
    return [
        schema_validates(narrative),
        paragraph_count_3_to_5_each_lang(narrative),
        russian_actually_cyrillic(narrative),
        word_count_ratio_en_ru_in_0_7_to_1_4(narrative),
        title_under_60_chars(narrative),
        subtitle_under_90_chars(narrative),
        milestone_under_30_chars(narrative),
        indices_valid(narrative, n_photos),
        pull_quote_drawn_from_body(narrative),
    ]


# -- internal helpers ---------------------------------------------------------


def _words(text: str) -> list[str]:
    """Return lowercased word tokens (letters/digits) from ``text``."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _max_ascii_letter_run(text: str) -> int:
    """Length of the longest consecutive run of whitespace-separated tokens
    that, after stripping non-letter characters, are non-empty and pure ASCII.

    Tokens that strip to empty (numbers, punctuation-only) reset the run, so
    Russian text peppered with ``5 km`` does not trip the check.
    """
    longest = 0
    current = 0
    for token in text.split():
        stripped = "".join(ch for ch in token if ch.isalpha())
        if stripped and stripped.isascii():
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def _length_check(name: str, limit: int, *fields: tuple[str, str]) -> RubricResult:
    """Pass when every named field is strictly shorter than ``limit`` chars."""
    over = [(label, len(value)) for label, value in fields if len(value) >= limit]
    if not over:
        sizes = ", ".join(f"{label}={len(value)}" for label, value in fields)
        return RubricResult(name, True, sizes)
    bits = ", ".join(f"{label}={size}" for label, size in over)
    return RubricResult(name, False, f"{bits} (limit {limit})")
