"""Free unit tests for the ADR-017 place-stitch rubric.

These exercise every check in ``tests.eval.place_rubric`` on hand-built
``PlaceContext`` fixtures — deterministic, no LLM, runs in ``make ci``.
This is the always-on regression net; the paid runner
``tests.eval.run_place`` applies the same rubric to a real stitch call.
"""

from __future__ import annotations

from tests.eval.place_rubric import (
    apply_place_rubric,
    banned_substrings_en,
    russian_summary_cyrillic,
    summary_grounded_in_sources,
    summary_length_band_each_lang,
    summary_present_each_lang,
    town_named_en,
    used_details_are_real_beats,
)
from trailstory.models import LocalizedString, PlaceContext
from trailstory.place import PlaceReference

_GOOD_EXTRACT = (
    "Bad Tölz is a town in Bavaria and the administrative center of its "
    "district, on the river Isar."
)
_GOOD_EN = (
    "Bad Tölz is the administrative center of its Bavarian district, on the "
    "river Isar; you passed the strange wax-figure church on your walk."
)
_GOOD_RU = (
    "Бад-Тёльц — административный центр района в Баварии, на реке Изар; "
    "вы прошли мимо странной церкви с восковыми фигурами."  # noqa: RUF001
)
_GOOD_DE = (
    "Bad Tölz ist das Verwaltungszentrum seines bayerischen Landkreises an "
    "der Isar; ihr seid an der seltsamen Wachsfiguren-Kirche vorbeigegangen."
)
_BEATS = ["river walk", "brunch", "the wax-figure church", "that strange wax-figure church"]


def _ref(extract: str = _GOOD_EXTRACT) -> PlaceReference:
    return PlaceReference(
        town="Bad Tölz",
        region="Bavarian Prealps",
        extract=extract,
        source_url="https://en.wikipedia.org/wiki/Bad_T%C3%B6lz",
        source_title="Bad Tölz",
    )


def _ctx(
    *,
    en: str = _GOOD_EN,
    ru: str = _GOOD_RU,
    de: str = _GOOD_DE,
    town: str = "Bad Tölz",
    used: list[str] | None = None,
) -> PlaceContext:
    return PlaceContext(
        town=town,
        region="Bavarian Prealps",
        summary=LocalizedString(en=en, ru=ru, de=de),
        used_hiker_details=used if used is not None else ["the wax-figure church"],
        source_url="https://en.wikipedia.org/wiki/Bad_T%C3%B6lz",
        source_title="Bad Tölz",
    )


def test_good_context_passes_all() -> None:
    results = apply_place_rubric(_ctx(), reference=_ref(), beats=_BEATS)
    failed = [(r.name, r.detail) for r in results if not r.passed]
    assert not failed, f"unexpected failures: {failed}"


def test_summary_present_fails_on_empty_lang() -> None:
    assert not summary_present_each_lang(_ctx(ru="   ")).passed


def test_length_band_fails_when_too_short() -> None:
    assert not summary_length_band_each_lang(_ctx(en="Short.")).passed


def test_length_band_fails_when_too_long() -> None:
    assert not summary_length_band_each_lang(_ctx(en="x " * 300)).passed


def test_town_named_en_fails_when_absent() -> None:
    assert not town_named_en(_ctx(en="A market town on a river in the foothills.")).passed


def test_russian_cyrillic_fails_on_latin() -> None:
    assert not russian_summary_cyrillic(
        _ctx(ru="Bad Tolz is a small market town in the Bavarian foothills.")
    ).passed


def test_banned_substrings_fails_on_tic() -> None:
    assert not banned_substrings_en(
        _ctx(en="Bad Tölz is the kind of town you remember for a long while.")
    ).passed


def test_used_details_fails_on_invented_beat() -> None:
    assert not used_details_are_real_beats(_ctx(used=["a castle we never saw"]), _BEATS).passed


def test_used_details_passes_on_real_beats() -> None:
    assert used_details_are_real_beats(_ctx(used=["river walk"]), _BEATS).passed


def test_grounded_fails_on_ungrounded_summary() -> None:
    ungrounded = (
        "Bad Tölz hosts a famous annual jazz festival beneath ancient volcanic "
        "cliffs near sprawling coastal vineyards."
    )
    assert not summary_grounded_in_sources(_ctx(en=ungrounded), _ref(), _BEATS).passed


def test_grounded_passes_on_grounded_summary() -> None:
    assert summary_grounded_in_sources(_ctx(), _ref(), _BEATS).passed
