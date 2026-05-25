"""Unit tests for the narrative-quality rubric.

Each rubric check is exercised on hand-built ``NarrativeOutput``
fixtures: one that should pass and at least one that should fail. These
tests run in ``make ci`` and never call the LLM — they're the cheap,
always-on guard against regressions in the rubric itself.

The paid eval that runs the rubric against real model output lives at
``tests/eval/run.py`` and is invoked by ``make eval``; it is intentionally
not part of CI.
"""

from __future__ import annotations

import pytest

from tests.conftest import paragraphs_from_strings
from tests.eval import rubric
from tests.eval.rubric import RubricResult
from trailstory.models import (
    LocalizedString,
    NarrativeOutput,
    Sentence,
)


def _good_narrative() -> NarrativeOutput:
    """A NarrativeOutput crafted to pass every rubric check.

    Each test below derives negative fixtures from this baseline via
    ``model_copy(update=...)`` so a single failing field is the only
    thing the assertion is responding to.
    """
    return NarrativeOutput(
        schema_version=3,
        title=LocalizedString(
            en="Above the fog line",
            ru="Над линией тумана",
            de="Über der Nebelgrenze",
        ),
        subtitle=LocalizedString(
            en="A quiet morning above the cloud sea",
            ru="Тихое утро над морем облаков",
            de="Ein stiller Morgen über dem Wolkenmeer",
        ),
        paragraphs=paragraphs_from_strings(
            en=[
                "We left the trailhead at first light, the air sharp with damp moss.",
                "By the saddle the cloud was thinning into a soft white scarf.",
                "Mia slept the whole climb, her cheek warm against the carrier.",
                "At the ridge the fog cleared and the valley opened beneath us.",
            ],
            ru=[
                "Вышли на тропу с первыми лучами; воздух пах мхом и хвоей.",  # noqa: RUF001
                "К седловине облака уже редели, превращаясь в белый шарф.",  # noqa: RUF001
                "Мия проспала весь подъём, прижавшись щекой к переноске.",
                "На хребте туман рассеялся, и долина раскинулась под нами.",  # noqa: RUF001
            ],
            de=[
                "Bei erstem Licht brachen wir auf, die Luft scharf von feuchtem Moos.",
                "Am Sattel zog die Wolke sich zu einem weichen weißen Schal zusammen.",
                "Mia schlief den ganzen Aufstieg, die Wange warm an der Trage.",
                "Am Grat lichtete sich der Nebel und das Tal öffnete sich unter uns.",
            ],
        ),
        pull_quote=LocalizedString(
            en="The fog cleared and the valley opened beneath us.",
            ru="Туман рассеялся, и долина раскинулась под нами.",
            de="Der Nebel lichtete sich und das Tal öffnete sich unter uns.",
        ),
        milestone=LocalizedString(
            en="First mountain hike",
            ru="Первый горный поход",
            de="Erste Bergwanderung",
        ),
        selected_photo_indices=[0, 1, 2, 3, 4, 5],
    )


def _replace(
    narrative: NarrativeOutput,
    *,
    field: str,
    lang: str,
    value: object,
) -> NarrativeOutput:
    """Return a copy of ``narrative`` with one localized leaf swapped.

    e.g. ``_replace(n, field="title", lang="en", value="x")`` changes
    only ``narrative.title.en``. Saves a lot of repetitive
    ``model_copy(update={...})`` boilerplate in the per-field tests.
    """
    container = getattr(narrative, field)
    updated_container = container.model_copy(update={lang: value})
    return narrative.model_copy(update={field: updated_container})


# ── schema_validates ────────────────────────────────────────────────────────


def test_schema_validates_passes_on_well_formed_narrative() -> None:
    result = rubric.schema_validates(_good_narrative())
    assert isinstance(result, RubricResult)
    assert result.name == "schema_validates"
    assert result.passed is True


def test_schema_validates_fails_when_field_type_is_corrupted() -> None:
    """Round-trip catches values that bypassed Pydantic validation."""
    # ``model_copy(update=...)`` does not re-validate — we use it here to
    # simulate the corrupted-state failure mode the check exists to detect.
    corrupted = _good_narrative().model_copy(
        update={"paragraphs": "not a at all"},
    )
    result = rubric.schema_validates(corrupted)
    assert result.passed is False
    assert result.detail  # carries the validation-error message


# ── paragraph_count_3_to_5_each_lang ────────────────────────────────────────


def test_paragraph_count_passes_when_each_lang_in_range() -> None:
    result = rubric.paragraph_count_3_to_5_each_lang(_good_narrative())
    assert result.passed is True


def test_paragraph_count_fails_when_too_few() -> None:
    """ADR-014: paragraphs is one list shared across languages, so the
    EN-too-few / RU-too-many / DE-too-few failure modes collapse to one:
    the paragraph list length is outside the 3-5 range. Tri-lingual
    mismatch is now impossible by construction."""
    base = _good_narrative()
    narrative = base.model_copy(update={"paragraphs": base.paragraphs[:2]})
    result = rubric.paragraph_count_3_to_5_each_lang(narrative)
    assert result.passed is False
    assert "en=2" in result.detail and "ru=2" in result.detail and "de=2" in result.detail


def test_paragraph_count_fails_when_too_many() -> None:
    base = _good_narrative()
    # _good_narrative has 4 paragraphs; tripling gives 12.
    narrative = base.model_copy(
        update={"paragraphs": base.paragraphs + base.paragraphs + base.paragraphs}
    )
    result = rubric.paragraph_count_3_to_5_each_lang(narrative)
    assert result.passed is False
    assert "en=12" in result.detail


# ── russian_actually_cyrillic ────────────────────────────────────────────────


def test_russian_actually_cyrillic_passes_on_pure_cyrillic() -> None:
    result = rubric.russian_actually_cyrillic(_good_narrative())
    assert result.passed is True


def _replace_ru_paragraph(narrative: NarrativeOutput, index: int, new_ru: str) -> NarrativeOutput:
    """Helper for ADR-014 sentence-level structure: rewrite the RU text of
    paragraph ``index`` to ``new_ru`` while keeping EN/DE intact. The
    paragraph becomes a single sentence carrying the replacement text."""
    paragraphs = list(narrative.paragraphs)
    # Reuse the first sentence's EN + DE; replace RU with the test fixture.
    first_sent = paragraphs[index][0]
    paragraphs[index] = [
        Sentence(
            text=LocalizedString(en=first_sent.text.en, ru=new_ru, de=first_sent.text.de),
            provenance=first_sent.provenance,
        )
    ]
    return narrative.model_copy(update={"paragraphs": paragraphs})


def test_russian_actually_cyrillic_fails_when_paragraph_has_no_cyrillic() -> None:
    narrative = _replace_ru_paragraph(
        _good_narrative(), 1, "This whole paragraph stayed in English by mistake."
    )
    result = rubric.russian_actually_cyrillic(narrative)
    assert result.passed is False
    assert "no Cyrillic" in result.detail


def test_russian_actually_cyrillic_fails_on_long_ascii_run_inside_cyrillic_paragraph() -> None:
    """Cyrillic prefix, then a 6+ word English clause — the failure mode this catches."""
    narrative = _replace_ru_paragraph(
        _good_narrative(), 2, "Тропа. We then walked back down the long stony path."
    )
    result = rubric.russian_actually_cyrillic(narrative)
    assert result.passed is False
    assert "ASCII" in result.detail


def test_russian_actually_cyrillic_tolerates_short_ascii_inserts() -> None:
    """A few ASCII words ('5 km', a name) inside a Cyrillic paragraph is fine."""
    narrative = _replace_ru_paragraph(_good_narrative(), 0, "Прошли 5 km вдоль ручья — Mia спала.")
    result = rubric.russian_actually_cyrillic(narrative)
    assert result.passed is True


# ── word_count_ratio_en_ru_in_0_7_to_1_4 ─────────────────────────────────────


def test_word_count_ratio_passes_when_balanced() -> None:
    result = rubric.word_count_ratio_en_ru_in_0_7_to_1_4(_good_narrative())
    assert result.passed is True


def _replace_paragraph_text(
    narrative: NarrativeOutput,
    *,
    en: list[str] | None = None,
    ru: list[str] | None = None,
    de: list[str] | None = None,
) -> NarrativeOutput:
    """Replace per-language paragraph text while keeping the structure.

    For each provided language, paragraph ``i`` becomes a single sentence
    whose text in that language is ``[i]``. Languages not supplied keep
    their original sentence joined-text.
    """
    new_paragraphs: list[list[Sentence]] = []
    for i, paragraph in enumerate(narrative.paragraphs):
        orig = paragraph[0]
        new_paragraphs.append(
            [
                Sentence(
                    text=LocalizedString(
                        en=en[i] if en is not None and i < len(en) else orig.text.en,
                        ru=ru[i] if ru is not None and i < len(ru) else orig.text.ru,
                        de=de[i] if de is not None and i < len(de) else orig.text.de,
                    ),
                    provenance=orig.provenance,
                )
            ]
        )
    return narrative.model_copy(update={"paragraphs": new_paragraphs})


def test_word_count_ratio_fails_when_ru_far_shorter_than_en() -> None:
    narrative = _replace_paragraph_text(_good_narrative(), ru=["Коротко.", "Очень.", "Тихо."])
    result = rubric.word_count_ratio_en_ru_in_0_7_to_1_4(narrative)
    assert result.passed is False
    assert "ratio=" in result.detail


def test_word_count_ratio_fails_when_en_paragraphs_have_no_words() -> None:
    narrative = _replace_paragraph_text(_good_narrative(), en=["", "", ""])
    result = rubric.word_count_ratio_en_ru_in_0_7_to_1_4(narrative)
    assert result.passed is False


# ── word_count_ratio_en_de_in_0_7_to_1_4 ─────────────────────────────────────


def test_word_count_ratio_en_de_passes_when_balanced() -> None:
    result = rubric.word_count_ratio_en_de_in_0_7_to_1_4(_good_narrative())
    assert result.passed is True


def test_word_count_ratio_en_de_fails_when_de_far_shorter_than_en() -> None:
    narrative = _replace_paragraph_text(_good_narrative(), de=["Kurz.", "Sehr.", "Still."])
    result = rubric.word_count_ratio_en_de_in_0_7_to_1_4(narrative)
    assert result.passed is False
    assert "ratio=" in result.detail


# ── title / subtitle / milestone length checks ──────────────────────────────


def test_title_under_60_chars_passes_under_limit() -> None:
    result = rubric.title_under_60_chars(_good_narrative())
    assert result.passed is True


def test_title_under_60_chars_fails_when_en_too_long() -> None:
    narrative = _replace(_good_narrative(), field="title", lang="en", value="x" * 61)
    result = rubric.title_under_60_chars(narrative)
    assert result.passed is False
    assert "title.en=61" in result.detail


def test_title_under_60_chars_fails_at_exactly_the_limit() -> None:
    """Limit is exclusive — len == 60 is too long."""
    narrative = _replace(_good_narrative(), field="title", lang="en", value="x" * 60)
    result = rubric.title_under_60_chars(narrative)
    assert result.passed is False


def test_title_under_60_chars_fails_when_de_too_long() -> None:
    narrative = _replace(_good_narrative(), field="title", lang="de", value="x" * 61)
    result = rubric.title_under_60_chars(narrative)
    assert result.passed is False
    assert "title.de=61" in result.detail


def test_subtitle_under_90_chars_passes_under_limit() -> None:
    result = rubric.subtitle_under_90_chars(_good_narrative())
    assert result.passed is True


def test_subtitle_under_90_chars_fails_when_ru_too_long() -> None:
    narrative = _replace(_good_narrative(), field="subtitle", lang="ru", value="ы" * 91)
    result = rubric.subtitle_under_90_chars(narrative)
    assert result.passed is False
    assert "subtitle.ru=91" in result.detail


def test_milestone_under_30_chars_passes_under_limit() -> None:
    result = rubric.milestone_under_30_chars(_good_narrative())
    assert result.passed is True


def test_milestone_under_30_chars_fails_when_too_long() -> None:
    narrative = _replace(
        _good_narrative(),
        field="milestone",
        lang="en",
        value="First mountain hike with the baby and the dog",
    )
    result = rubric.milestone_under_30_chars(narrative)
    assert result.passed is False


# ── indices_valid ────────────────────────────────────────────────────────────


def test_indices_valid_passes_with_six_unique_in_range() -> None:
    result = rubric.indices_valid(_good_narrative(), n_photos=12)
    assert result.passed is True


def test_indices_valid_passes_at_upper_count_bound() -> None:
    narrative = _good_narrative().model_copy(
        update={"selected_photo_indices": [0, 1, 2, 3, 4, 5, 6, 7]},
    )
    result = rubric.indices_valid(narrative, n_photos=12)
    assert result.passed is True


def test_indices_valid_fails_with_too_few_indices() -> None:
    narrative = _good_narrative().model_copy(
        update={"selected_photo_indices": [0, 1, 2, 3, 4]},
    )
    result = rubric.indices_valid(narrative, n_photos=12)
    assert result.passed is False
    assert "5 indices" in result.detail


def test_indices_valid_fails_with_too_many_indices() -> None:
    narrative = _good_narrative().model_copy(
        update={"selected_photo_indices": [0, 1, 2, 3, 4, 5, 6, 7, 8]},
    )
    result = rubric.indices_valid(narrative, n_photos=12)
    assert result.passed is False
    assert "9 indices" in result.detail


def test_indices_valid_fails_with_out_of_range_index() -> None:
    narrative = _good_narrative().model_copy(
        update={"selected_photo_indices": [0, 1, 2, 3, 4, 99]},
    )
    result = rubric.indices_valid(narrative, n_photos=10)
    assert result.passed is False
    assert "out-of-range" in result.detail


def test_indices_valid_fails_with_duplicate_index() -> None:
    narrative = _good_narrative().model_copy(
        update={"selected_photo_indices": [0, 0, 1, 2, 3, 4]},
    )
    result = rubric.indices_valid(narrative, n_photos=10)
    assert result.passed is False
    assert "duplicate" in result.detail


# ── pull_quote_drawn_from_body ──────────────────────────────────────────────


def test_pull_quote_passes_when_substring_of_paragraph() -> None:
    result = rubric.pull_quote_drawn_from_body(_good_narrative())
    assert result.passed is True


def test_pull_quote_fails_when_words_not_in_any_paragraph() -> None:
    narrative = _replace(
        _good_narrative(),
        field="pull_quote",
        lang="en",
        value="Entirely synthetic phrase nobody wrote.",
    )
    result = rubric.pull_quote_drawn_from_body(narrative)
    assert result.passed is False
    assert "max overlap" in result.detail


def test_pull_quote_fails_on_empty_quote() -> None:
    narrative = _replace(_good_narrative(), field="pull_quote", lang="en", value="")
    result = rubric.pull_quote_drawn_from_body(narrative)
    assert result.passed is False
    assert "no words" in result.detail


# ── apply_rubric (orchestrator) ─────────────────────────────────────────────


def test_apply_rubric_returns_one_result_per_check_in_order() -> None:
    results = rubric.apply_rubric(_good_narrative(), n_photos=12)
    expected = [
        "schema_validates",
        "paragraph_count_3_to_5_each_lang",
        "russian_actually_cyrillic",
        "word_count_ratio_en_ru_in_0_7_to_1_4",
        "word_count_ratio_en_de_in_0_7_to_1_4",
        "title_under_60_chars",
        "subtitle_under_90_chars",
        "milestone_under_30_chars",
        "indices_valid",
        "pull_quote_drawn_from_body",
    ]
    assert [r.name for r in results] == expected


def test_apply_rubric_all_pass_for_well_formed_narrative() -> None:
    results = rubric.apply_rubric(_good_narrative(), n_photos=12)
    failed = [r for r in results if not r.passed]
    assert failed == [], f"expected all checks to pass, got failures: {failed}"


@pytest.mark.parametrize(
    "field,lang,value,expected_failing_check",
    [
        ("title", "en", "x" * 70, "title_under_60_chars"),
        ("subtitle", "ru", "ы" * 100, "subtitle_under_90_chars"),
    ],
)
def test_apply_rubric_surfaces_targeted_localized_failure(
    field: str,
    lang: str,
    value: object,
    expected_failing_check: str,
) -> None:
    narrative = _replace(_good_narrative(), field=field, lang=lang, value=value)
    results = rubric.apply_rubric(narrative, n_photos=12)
    by_name = {r.name: r for r in results}
    assert by_name[expected_failing_check].passed is False


@pytest.mark.parametrize(
    "field,value,expected_failing_check",
    [
        ("selected_photo_indices", [0, 1, 2], "indices_valid"),
    ],
)
def test_apply_rubric_surfaces_top_level_failure(
    field: str,
    value: object,
    expected_failing_check: str,
) -> None:
    narrative = _good_narrative().model_copy(update={field: value})
    results = rubric.apply_rubric(narrative, n_photos=12)
    by_name = {r.name: r for r in results}
    assert by_name[expected_failing_check].passed is False


def test_apply_rubric_surfaces_disjoint_pull_quote() -> None:
    narrative = _replace(
        _good_narrative(),
        field="pull_quote",
        lang="en",
        value="Wholly disjoint synthetic words.",
    )
    results = rubric.apply_rubric(narrative, n_photos=12)
    by_name = {r.name: r for r in results}
    assert by_name["pull_quote_drawn_from_body"].passed is False
