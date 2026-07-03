"""Programmatic rubric for the ADR-017 place-context stitch.

Mirrors :mod:`tests.eval.rubric` for the narrative writer: each check
inspects a :class:`~trailstory.models.PlaceContext` (plus the grounded
inputs it was built from — the :class:`~trailstory.place.PlaceReference`
and the hiker's beats) and returns a single :class:`RubricResult`.

The rubric is deliberately mechanical — no LLM calls. That keeps it free,
fast, deterministic, and unit-tested in ``make ci`` (see
``tests/test_eval_place_rubric.py``). The paid runner
:mod:`tests.eval.run_place` applies the same checks to the output of a
**real** stitch call.

The faithfulness contract (ADR-017: facts are sourced, not generated) is
guarded here two ways, both free: ``used_details_are_real_beats`` rejects a
stitch that claims a beat it was never given, and
``summary_grounded_in_sources`` flags an EN summary whose significant
content words mostly do not trace to the supplied extract / beats / place
name. Deep claim-level faithfulness stays a (future) paid-judge concern,
exactly as it is for the narrative.
"""

from __future__ import annotations

import re

from tests.eval.rubric import (
    _BANNED_EN,  # shared voice gate with the narrative rubric (ADR-016)
    RubricResult,
)
from trailstory.models import PlaceContext
from trailstory.place import PlaceReference

# A place note is 2-3 sentences. Per-language char band: below the floor it
# is a stub, above the ceiling it has drifted into an encyclopedia entry.
_SUMMARY_MIN_CHARS: int = 40
_SUMMARY_MAX_CHARS: int = 420

# Cyrillic block + supplement, mirroring the narrative rubric.
_CYRILLIC_RE: re.Pattern[str] = re.compile("[Ѐ-ԯ]")
_WORD_RE: re.Pattern[str] = re.compile(r"[^\W_]+", flags=re.UNICODE)

# Connective / generic geographic vocabulary a grounded summary may legitimately
# use even when the word is not literally in the reference extract or beats.
# Keeps ``summary_grounded_in_sources`` from flagging ordinary framing words.
_GENERIC_ALLOW: frozenset[str] = frozenset(
    {
        "town",
        "city",
        "village",
        "market",
        "river",
        "valley",
        "hill",
        "hills",
        "mountain",
        "mountains",
        "foothills",
        "lake",
        "small",
        "near",
        "centre",
        "center",
        "region",
        "area",
        "place",
        "walk",
        "walked",
        "hike",
        "hiked",
        "passed",
        "along",
        "through",
        "around",
        "where",
        "which",
        "their",
        "there",
        "here",
    }
)

# Share of significant EN content words that must trace to the grounding set.
# Loose on purpose — this is a gross-fabrication sanity gate, not a tight
# claim-by-claim faithfulness check (that is the paid judge's job).
_GROUNDED_RATIO_MIN: float = 0.5
# Below this many significant words there is not enough signal to judge
# grounding, so the check passes rather than firing on noise.
_GROUNDED_MIN_WORDS: int = 4


def summary_present_each_lang(pc: PlaceContext) -> RubricResult:
    """EN, RU, and DE summaries must each be non-empty after stripping."""
    name = "summary_present_each_lang"
    missing = [lang for lang in ("en", "ru", "de") if not getattr(pc.summary, lang).strip()]
    if missing:
        return RubricResult(name, False, f"empty summary for: {', '.join(missing)}")
    return RubricResult(name, True, "ok")


def summary_length_band_each_lang(pc: PlaceContext) -> RubricResult:
    """Each summary's character length must sit in ``[MIN, MAX]``."""
    name = "summary_length_band_each_lang"
    sizes = {lang: len(getattr(pc.summary, lang).strip()) for lang in ("en", "ru", "de")}
    detail = ", ".join(f"{k}={v}" for k, v in sizes.items())
    out = {k: v for k, v in sizes.items() if not _SUMMARY_MIN_CHARS <= v <= _SUMMARY_MAX_CHARS}
    if out:
        return RubricResult(
            name, False, f"{detail}; expected {_SUMMARY_MIN_CHARS}-{_SUMMARY_MAX_CHARS} chars"
        )
    return RubricResult(name, True, detail)


def town_named_en(pc: PlaceContext) -> RubricResult:
    """The town name must appear in the English summary.

    Only EN is checked: the stitch transliterates the place name per
    language (ADR-017 prompt — Russian renders "Бад-Тёльц"), so a literal
    ``pc.town`` substring is reliable only in English.
    """
    name = "town_named_en"
    if pc.town.lower() in pc.summary.en.lower():
        return RubricResult(name, True, f"'{pc.town}' present in summary.en")
    return RubricResult(name, False, f"town '{pc.town}' missing from summary.en")


def russian_summary_cyrillic(pc: PlaceContext) -> RubricResult:
    """RU summary must contain Cyrillic and no run of > 5 ASCII-letter words."""
    name = "russian_summary_cyrillic"
    ru = pc.summary.ru
    if not _CYRILLIC_RE.search(ru):
        return RubricResult(name, False, "summary.ru has no Cyrillic char")
    run = _max_ascii_letter_run(ru)
    if run > 5:
        return RubricResult(name, False, f"summary.ru has a run of {run} ASCII-letter words")
    return RubricResult(name, True, "ok")


def banned_substrings_en(pc: PlaceContext) -> RubricResult:
    """Hard gate on the shared "LLM travel-essay" phrases (ADR-016) in EN."""
    name = "banned_substrings_en"
    haystack = pc.summary.en.lower()
    hits = sorted({phrase for phrase in _BANNED_EN if phrase.lower() in haystack})
    if hits:
        return RubricResult(name, False, f"banned phrase(s) present: {', '.join(hits)}")
    return RubricResult(name, True, f"none of {len(_BANNED_EN)} banned phrase(s) present")


def used_details_are_real_beats(pc: PlaceContext, beats: list[str]) -> RubricResult:
    """Every ``used_hiker_details`` entry must be one of the supplied beats.

    A faithfulness gate: the stitch reports which lived beats it wove in;
    if it reports one it was never given, it invented the provenance.
    """
    name = "used_details_are_real_beats"
    beat_set = {b.strip().lower() for b in beats}
    invented = [d for d in pc.used_hiker_details if d.strip().lower() not in beat_set]
    if invented:
        return RubricResult(name, False, f"used_hiker_details not in beats: {invented}")
    return RubricResult(name, True, f"{len(pc.used_hiker_details)} detail(s), all real beats")


def summary_grounded_in_sources(
    pc: PlaceContext, reference: PlaceReference, beats: list[str]
) -> RubricResult:
    """Most significant EN content words must trace to the grounding set.

    The grounding set is the reference extract + beats + town + region.
    "Significant" = length ≥ 5 and not a generic framing word. A low share
    of grounded significant words is the signature of a summary that
    reached past its sources. Loose gate (see ``_GROUNDED_RATIO_MIN``).
    """
    name = "summary_grounded_in_sources"
    grounding = _word_set(reference.extract) | _word_set(reference.town)
    grounding |= _word_set(reference.region or "")
    for b in beats:
        grounding |= _word_set(b)
    # ADR-019: a supplied OSM landmark name the stitch may have used
    # ("Mühlfeldkirche") is a sourced fact, not fabrication — ground it.
    for landmark in pc.named_landmarks:
        grounding |= _word_set(landmark.name)

    significant = [w for w in _words(pc.summary.en) if len(w) >= 5 and w not in _GENERIC_ALLOW]
    if len(significant) < _GROUNDED_MIN_WORDS:
        return RubricResult(name, True, f"only {len(significant)} significant words; skipped")
    grounded = [w for w in significant if w in grounding]
    ratio = len(grounded) / len(significant)
    detail = f"ratio={ratio:.2f} ({len(grounded)}/{len(significant)})"
    if ratio >= _GROUNDED_RATIO_MIN:
        return RubricResult(name, True, detail)
    ungrounded = sorted(set(significant) - grounding)
    return RubricResult(
        name, False, f"{detail}; expected ≥ {_GROUNDED_RATIO_MIN}; ungrounded={ungrounded}"
    )


def apply_place_rubric(
    pc: PlaceContext, *, reference: PlaceReference, beats: list[str]
) -> list[RubricResult]:
    """Run every place-rubric check and return the results in display order."""
    return [
        summary_present_each_lang(pc),
        summary_length_band_each_lang(pc),
        town_named_en(pc),
        russian_summary_cyrillic(pc),
        banned_substrings_en(pc),
        used_details_are_real_beats(pc, beats),
        summary_grounded_in_sources(pc, reference, beats),
    ]


# -- internal helpers ---------------------------------------------------------


def _words(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _word_set(text: str) -> set[str]:
    return set(_words(text))


def _max_ascii_letter_run(text: str) -> int:
    longest = 0
    current = 0
    for token in text.split():
        stripped = "".join(ch for ch in token if ch.isalpha())
        if stripped and stripped.isascii():
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
