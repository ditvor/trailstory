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

from trailstory.models import NarrativeOutput, ProvenanceSource

# Cyrillic block (U+0400-U+04FF) plus Cyrillic Supplement (U+0500-U+052F).
# Spelled as Unicode escapes so the range is unambiguous in any terminal.
_CYRILLIC_RE: re.Pattern[str] = re.compile("[\u0400-\u052f]")

# Word tokens for overlap and ratio counting: any maximal run of letters or
# digits, ignoring underscores and punctuation. ``\W`` is locale-aware under
# ``re.UNICODE``, so this captures both Latin and Cyrillic words.
_WORD_RE: re.Pattern[str] = re.compile(r"[^\W_]+", flags=re.UNICODE)

# Crude sentence splitter: anything between two terminal-punctuation
# tokens. Good enough for the rubric \u2014 we want average sentence length,
# not perfect linguistic parsing. RU/DE share the same period set as EN.
# Periods that are NOT sentence boundaries are masked first by
# ``_split_sentences``: a period after a single free-standing letter
# (German abbreviations "z. B.", "d. h."; Russian initials "\u0410. \u0421.")
# and a period between digits ("3.5 km"). Without the masking, German
# prose containing "z. B." gets chopped into pseudo-sentences and the
# average length collapses \u2014 a false rubric failure on perfectly normal
# German.
_SENTENCE_SPLIT_RE: re.Pattern[str] = re.compile(r"[.!?\u2026]+")
_SINGLE_LETTER_DOT_RE: re.Pattern[str] = re.compile(r"(^|\s)(\w)\.", flags=re.UNICODE)
_DECIMAL_DOT_RE: re.Pattern[str] = re.compile(r"(\d)\.(\d)")
_MASK = "\x00"

# ADR-015 style gate \u2014 phrases that mark the "LLM travel-essay" voice
# the user has flagged in PR56 and subsequent reviews. Each list is the
# banned vocabulary FOR THAT LANGUAGE; an empty list means "no gate yet,
# the user will populate after seeing eval output." Hard gate: any
# golden containing any of these substrings (case-insensitive) fails
# the rubric and the build. Strings are matched literally so add the
# whole tic ("doing its quiet work") rather than a word ("quiet").
#
# When adding a phrase, prefer one with no plausible legitimate use in
# a warm family-note register. A blanket "magical" ban would fire on
# "this magical place" which can be genuine; "earned itself" only ever
# means literary scaffolding.
_BANNED_EN: tuple[str, ...] = (
    "unspool",  # "the trail unspooled", "the path unspooled"
    "the kind of",  # generic literary scaffolding ("the kind of light that\u2026")
    "earned itself",  # "the day earned itself"
    "doing its quiet work",  # "the river doing its quiet work"
    "memory of cold",  # specific tic flagged in PR56
    "memory of warmth",  # symmetric pre-emptive ban
)
_BANNED_RU: tuple[str, ...] = ()  # populate after eval observation
_BANNED_DE: tuple[str, ...] = ()  # populate after eval observation

# Average sentence length (words per sentence) acceptable band per
# language. Below 6: clipped meeting-minutes register. Above 24: long
# winding literary register. This is a signal, not a hard product
# requirement \u2014 sentence length is a proxy for cadence, and rubric
# failure here means "voice has drifted to one extreme."
_AVG_SENTENCE_LEN_MIN: float = 6.0
_AVG_SENTENCE_LEN_MAX: float = 24.0

# Maximum acceptable share of sentences self-reported as INFERRED. The
# verifier loop (ADR-011) regenerates above ``Settings.max_inferred_ratio``
# (default 0.5). This rubric ceiling is intentionally looser (0.6) \u2014 a
# sanity check that catches narratives slipping past the verifier, not a
# tighter duplicate gate.
_INFERRED_RATIO_MAX: float = 0.6


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
    """EN, RU, and DE paragraph lists must each contain 3 to 5 entries.

    ADR-014: paragraphs are now ``list[Paragraph]`` with sentence-level
    provenance. We count via ``paragraphs_as_localized()`` so the same
    rubric check works on the new shape; the count is the same across
    languages by construction (one paragraph entry per language is one
    paragraph object).
    """
    name = "paragraph_count_3_to_5_each_lang"
    flat = narrative.paragraphs_as_localized()
    en = len(flat.en)
    ru = len(flat.ru)
    de = len(flat.de)
    if 3 <= en <= 5 and 3 <= ru <= 5 and 3 <= de <= 5:
        return RubricResult(name, True, f"en={en}, ru={ru}, de={de}")
    return RubricResult(name, False, f"en={en}, ru={ru}, de={de} (expected 3-5 each)")


def russian_actually_cyrillic(narrative: NarrativeOutput) -> RubricResult:
    """RU paragraphs: each has ≥1 Cyrillic char and no run of >5 ASCII-letter words.

    The Cyrillic check rejects whole-paragraph translation failures. The
    ASCII-run check catches the common subtler failure: the model
    translates most of a paragraph but drops back into English for a
    sentence or two.
    """
    name = "russian_actually_cyrillic"
    flat = narrative.paragraphs_as_localized()
    for i, para in enumerate(flat.ru):
        if not _CYRILLIC_RE.search(para):
            return RubricResult(name, False, f"paragraphs.ru[{i}] has no Cyrillic char")
        run = _max_ascii_letter_run(para)
        if run > 5:
            return RubricResult(
                name,
                False,
                f"paragraphs.ru[{i}] contains a run of {run} consecutive ASCII-letter words",
            )
    return RubricResult(name, True, "ok")


def word_count_ratio_en_ru_in_0_7_to_1_4(narrative: NarrativeOutput) -> RubricResult:
    """Total RU words divided by total EN words must be in [0.7, 1.4]."""
    name = "word_count_ratio_en_ru_in_0_7_to_1_4"
    flat = narrative.paragraphs_as_localized()
    en_words = sum(len(_words(p)) for p in flat.en)
    ru_words = sum(len(_words(p)) for p in flat.ru)
    if en_words == 0:
        return RubricResult(name, False, "EN paragraphs have no words")
    ratio = ru_words / en_words
    detail = f"ratio={ratio:.2f} (en={en_words}, ru={ru_words})"
    if 0.7 <= ratio <= 1.4:
        return RubricResult(name, True, detail)
    return RubricResult(name, False, f"{detail}; expected 0.7-1.4")


def word_count_ratio_en_de_in_0_7_to_1_4(narrative: NarrativeOutput) -> RubricResult:
    """Total DE words divided by total EN words must be in [0.7, 1.4].

    Same shape as the EN/RU check — catches a DE paragraph block that is
    half-empty or wildly verbose relative to the source.
    """
    name = "word_count_ratio_en_de_in_0_7_to_1_4"
    flat = narrative.paragraphs_as_localized()
    en_words = sum(len(_words(p)) for p in flat.en)
    de_words = sum(len(_words(p)) for p in flat.de)
    if en_words == 0:
        return RubricResult(name, False, "EN paragraphs have no words")
    ratio = de_words / en_words
    detail = f"ratio={ratio:.2f} (en={en_words}, de={de_words})"
    if 0.7 <= ratio <= 1.4:
        return RubricResult(name, True, detail)
    return RubricResult(name, False, f"{detail}; expected 0.7-1.4")


def title_under_60_chars(narrative: NarrativeOutput) -> RubricResult:
    """All three titles must be under 60 characters."""
    return _length_check(
        "title_under_60_chars",
        60,
        ("title.en", narrative.title.en),
        ("title.ru", narrative.title.ru),
        ("title.de", narrative.title.de),
    )


def subtitle_under_90_chars(narrative: NarrativeOutput) -> RubricResult:
    """All three subtitles must be under 90 characters."""
    return _length_check(
        "subtitle_under_90_chars",
        90,
        ("subtitle.en", narrative.subtitle.en),
        ("subtitle.ru", narrative.subtitle.ru),
        ("subtitle.de", narrative.subtitle.de),
    )


def milestone_under_30_chars(narrative: NarrativeOutput) -> RubricResult:
    """All three milestone tags must be under 30 characters."""
    return _length_check(
        "milestone_under_30_chars",
        30,
        ("milestone.en", narrative.milestone.en),
        ("milestone.ru", narrative.milestone.ru),
        ("milestone.de", narrative.milestone.de),
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
    """``pull_quote.en`` shares ≥60% of its words with at least one EN paragraph.

    Set-based overlap: ``|quote ∩ paragraph| / |quote|`` taken as the max
    over all paragraphs. This catches pull-quotes that are made up wholesale
    rather than distilled from the body, while tolerating light paraphrase.
    """
    name = "pull_quote_drawn_from_body"
    quote_words = set(_words(narrative.pull_quote.en))
    if not quote_words:
        return RubricResult(name, False, "pull_quote.en has no words")
    best = 0.0
    flat = narrative.paragraphs_as_localized()
    for para in flat.en:
        para_words = set(_words(para))
        overlap = len(quote_words & para_words) / len(quote_words)
        if overlap > best:
            best = overlap
    if best >= 0.60:
        return RubricResult(name, True, f"max overlap = {best:.2f}")
    return RubricResult(name, False, f"max overlap = {best:.2f}; expected ≥ 0.60")


def banned_substrings_en(narrative: NarrativeOutput) -> RubricResult:
    """ADR-015: hard gate on known "LLM travel-essay" phrases in English."""
    return _banned_substring_check("banned_substrings_en", narrative, "en", _BANNED_EN)


def banned_substrings_ru(narrative: NarrativeOutput) -> RubricResult:
    """ADR-015: hard gate on Russian poetic tics (currently empty — populate
    after eval reveals the patterns)."""
    return _banned_substring_check("banned_substrings_ru", narrative, "ru", _BANNED_RU)


def banned_substrings_de(narrative: NarrativeOutput) -> RubricResult:
    """ADR-015: hard gate on German poetic tics (currently empty — populate
    after eval reveals the patterns)."""
    return _banned_substring_check("banned_substrings_de", narrative, "de", _BANNED_DE)


def avg_sentence_length_each_lang(narrative: NarrativeOutput) -> RubricResult:
    """ADR-015: average sentence length in each language must sit in
    ``[_AVG_SENTENCE_LEN_MIN, _AVG_SENTENCE_LEN_MAX]``.

    Below the floor: "meeting minutes" register (overcorrected). Above
    the ceiling: long winding literary register (the voice we're
    trying to move away from). Uses a crude period-based splitter —
    accurate enough for an average over multiple paragraphs.
    """
    name = "avg_sentence_length_each_lang"
    flat = narrative.paragraphs_as_localized()
    metrics: dict[str, float] = {}
    for lang in ("en", "ru", "de"):
        text = " ".join(getattr(flat, lang))
        sentences = _split_sentences(text)
        if not sentences:
            return RubricResult(name, False, f"no sentences in {lang}")
        avg = sum(len(_words(s)) for s in sentences) / len(sentences)
        metrics[lang] = avg
    detail = ", ".join(f"{k}={v:.1f}" for k, v in metrics.items())
    out_of_band = {
        k: v for k, v in metrics.items() if v < _AVG_SENTENCE_LEN_MIN or v > _AVG_SENTENCE_LEN_MAX
    }
    if out_of_band:
        return RubricResult(
            name,
            False,
            f"{detail}; expected {_AVG_SENTENCE_LEN_MIN:.0f}-{_AVG_SENTENCE_LEN_MAX:.0f} per lang",
        )
    return RubricResult(name, True, detail)


def inferred_ratio_within_ceiling(narrative: NarrativeOutput) -> RubricResult:
    """ADR-015: writer-self-reported INFERRED-sentence share must be
    ≤ :data:`_INFERRED_RATIO_MAX`.

    The verifier loop (ADR-011) already regenerates above
    ``Settings.max_inferred_ratio`` (default 0.5). This rubric ceiling
    is looser on purpose — it catches narratives that slipped past the
    verifier (e.g., when the verifier was disabled, or both attempts
    came back above the ceiling).
    """
    name = "inferred_ratio_within_ceiling"
    total = 0
    inferred = 0
    for paragraph in narrative.paragraphs:
        for sentence in paragraph:
            total += 1
            if sentence.provenance.source == ProvenanceSource.INFERRED:
                inferred += 1
    if total == 0:
        return RubricResult(name, False, "no sentences in narrative")
    ratio = inferred / total
    detail = f"ratio={ratio:.2f} ({inferred}/{total})"
    if ratio <= _INFERRED_RATIO_MAX:
        return RubricResult(name, True, detail)
    return RubricResult(name, False, f"{detail}; expected ≤ {_INFERRED_RATIO_MAX:.2f}")


def apply_rubric(narrative: NarrativeOutput, n_photos: int) -> list[RubricResult]:
    """Run every rubric check and return the results in display order."""
    return [
        schema_validates(narrative),
        paragraph_count_3_to_5_each_lang(narrative),
        russian_actually_cyrillic(narrative),
        word_count_ratio_en_ru_in_0_7_to_1_4(narrative),
        word_count_ratio_en_de_in_0_7_to_1_4(narrative),
        title_under_60_chars(narrative),
        subtitle_under_90_chars(narrative),
        milestone_under_30_chars(narrative),
        indices_valid(narrative, n_photos),
        pull_quote_drawn_from_body(narrative),
        # ADR-015 style metrics:
        banned_substrings_en(narrative),
        banned_substrings_ru(narrative),
        banned_substrings_de(narrative),
        avg_sentence_length_each_lang(narrative),
        inferred_ratio_within_ceiling(narrative),
    ]


# -- internal helpers ---------------------------------------------------------


def _banned_substring_check(
    name: str,
    narrative: NarrativeOutput,
    lang: str,
    banned: tuple[str, ...],
) -> RubricResult:
    """Common implementation: case-insensitive substring search across
    body text + title / subtitle / pull_quote / milestone for one
    language. Empty banned list → always passes (no gate configured).
    """
    if not banned:
        return RubricResult(name, True, "no banned phrases configured")
    flat = narrative.paragraphs_as_localized()
    haystack = " ".join(getattr(flat, lang))
    for field in (narrative.title, narrative.subtitle, narrative.pull_quote, narrative.milestone):
        haystack += " " + getattr(field, lang)
    haystack = haystack.lower()
    hits = sorted({phrase for phrase in banned if phrase.lower() in haystack})
    if hits:
        return RubricResult(name, False, f"banned phrase(s) present: {', '.join(hits)}")
    return RubricResult(name, True, f"none of {len(banned)} banned phrase(s) present")


def _words(text: str) -> list[str]:
    """Return lowercased word tokens (letters/digits) from ``text``."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences, tolerating abbreviation periods.

    Masks the two period patterns that are not sentence boundaries —
    a period after a single free-standing letter (German "z. B.",
    "d. h.", Cyrillic name initials) and a period between digits
    ("3.5") — then splits on terminal punctuation and restores the
    masked periods. Word counting downstream is unaffected either way
    (the mask byte is not a word character), but restoring keeps the
    returned fragments readable in failure details.
    """
    masked = _SINGLE_LETTER_DOT_RE.sub(rf"\1\2{_MASK}", text)
    masked = _DECIMAL_DOT_RE.sub(rf"\1{_MASK}\2", masked)
    return [s.replace(_MASK, ".") for s in _SENTENCE_SPLIT_RE.split(masked) if s.strip()]


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
