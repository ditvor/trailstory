"""Tri-lingual builder UI metadata.

The builder's chrome (eyebrow, hero, section titles, button labels) is
served in English, Russian, and German simultaneously — the template
bakes all three into the page and a CSS attribute selector hides the
inactive two. See ``web/static/builder.css`` for the toggle rule.

This module owns:

* :data:`STYLE_CARDS` — display metadata for each style the picker
  surfaces, in the order they render. Names match the Claude Design
  proposal: The Letter / The Zine / Sunday / Postcard Set / Album.
  Only The Letter has a built renderer in v0; the other four are
  visible but ``coming_soon=True`` (rendered with a SOON pill, the
  radio is ``disabled``, and ``accepted_style_values()`` excludes
  them). The card ids mirror :class:`trailstory.models.Style`
  (the full lineup, ADR-021); the legacy ``log`` and
  ``encyclopedia`` renderers were removed in the same ADR.
* :data:`SUPPORTED_LANGS` — the three language codes the toggle exposes,
  in display order.

The COPY for everything *outside* the style picker lives directly in the
Jinja templates as ``<span class="lang-en/ru/de">…</span>`` so that
authors editing strings don't have to round-trip through a Python dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from web.pipeline import Style

SUPPORTED_LANGS: Final[tuple[str, ...]] = ("en", "ru", "de")
DEFAULT_LANG: Final[str] = "en"


@dataclass(frozen=True)
class StyleCard:
    """Display metadata for one card in the style picker.

    ``value`` matches a :class:`web.pipeline.Style` member for the
    buildable card (currently only ``letter`` for "The Letter"), or
    is a placeholder id for ``coming_soon=True`` cards whose renderer
    has not been built yet. The form refuses to accept a placeholder
    value — see :func:`accepted_style_values`.
    """

    value: str
    name_en: str
    name_ru: str
    name_de: str
    sub_en: str
    sub_ru: str
    sub_de: str
    desc_en: str
    desc_ru: str
    desc_de: str
    coming_soon: bool = False


# Order mirrors the design proposal's grid: The Letter, The Zine,
# Sunday, Postcard Set, Album.
STYLE_CARDS: Final[tuple[StyleCard, ...]] = (
    StyleCard(
        value=Style.letter.value,
        name_en="The Letter",
        name_ru="Письмо",
        name_de="Der Brief",
        sub_en="Editorial · magazine essay",
        sub_ru="Журнальное эссе",
        sub_de="Magazinaufsatz",
        desc_en="Long-form prose with a quiet voice. Marginalia for the map and stats. The version grandma reads with tea.",
        desc_ru="Длинное, тихое письмо. Карта и статистика на полях. Та версия, которую бабушка читает за чаем.",
        desc_de="Lange, ruhige Prosa. Karte und Statistik am Rand. Die Version, die Oma beim Tee liest.",
    ),
    StyleCard(
        value="zine",
        name_en="The Zine",
        name_ru="Зин",
        name_de="Das Zine",
        sub_en="Riso · two-color · loud",
        sub_ru="Ризограф · 2 цвета · громко",
        sub_de="Riso · zweifarbig · laut",
        desc_en="Indie zine. Halftone duotone photos, tall condensed display type, tape, terracotta spot color. Designed to be printed and mailed.",
        desc_ru="Инди-зин. Полутоновое дуо, узкий заголовочный шрифт, скотч, терракотовый акцент. Чтобы напечатать и отправить.",
        desc_de="Indie-Zine. Halftone-Duoton-Fotos, schmale Versalien, Tape, Terrakotta-Spotfarbe. Zum Drucken und Verschicken.",
        coming_soon=True,
    ),
    StyleCard(
        value="sunday",
        name_en="Sunday",
        name_ru="Воскресенье",
        name_de="Sonntag",
        sub_en="Joyful · warm cream + coral",
        sub_ru="Радостный · крем + коралл",
        sub_de="Heiter · creme + Koralle",
        desc_en="A good weekend day, in six stops. Cream, sun-yellow, and coral. Coral STOP badges and a sun on the cover.",
        desc_ru="Хороший выходной за шесть остановок. Крем, солнечно-жёлтый и коралловый. Коралловые значки СТОП и солнце на обложке.",
        desc_de="Ein guter Wochenendtag in sechs Stopps. Creme, Sonnengelb und Koralle. Korallenrote STOP-Marken und eine Sonne auf dem Cover.",
        coming_soon=True,
    ),
    StyleCard(
        value="postcard",
        name_en="Postcard Set",
        name_ru="Набор открыток",
        name_de="Postkarten-Set",
        sub_en="Vintage travel · seven cards",
        sub_ru="Винтаж · семь открыток",
        sub_de="Vintage · sieben Karten",
        desc_en="Mid-century travel cards. Each chapter is a postcard, front and back, with stamp, postmark, and an address line.",
        desc_ru="Винтажные открытки середины века. Каждая глава — открытка с двух сторон: марка, штемпель, строка адреса.",
        desc_de="Reisekarten der Mitte des Jahrhunderts. Jedes Kapitel als Postkarte vorn und hinten — Briefmarke, Stempel und Adresszeile.",
        coming_soon=True,
    ),
    StyleCard(
        value="album",
        name_en="Album",
        name_ru="Альбом",
        name_de="Album",
        sub_en="Scrapbook · polaroid + tape",
        sub_ru="Скрапбук · полароид + скотч",
        sub_de="Scrapbook · Polaroid + Tape",
        desc_en="Kept-in-a-shoebox feel. Polaroid-framed photos, washi tape, handwritten captions. The most intimate of the five.",
        desc_ru="Ощущение, что лежит в обувной коробке. Полароиды, цветной скотч, подписи от руки. Самый личный из пяти.",
        desc_de="Schuhkarton-Gefühl. Polaroidfotos, Washi-Tape, handschriftliche Bildunterschriften. Der intimste der fünf.",
        coming_soon=True,
    ),
)


def accepted_style_values() -> frozenset[str]:
    """The set of style ids the form is allowed to submit.

    ``coming_soon`` cards are visible in the picker but disabled; this
    set is the source of truth for what the route handler accepts.
    """
    return frozenset(c.value for c in STYLE_CARDS if not c.coming_soon)


def resolve_lang(candidate: str | None) -> str:
    """Coerce a user-supplied language code into one of SUPPORTED_LANGS.

    Falls back to :data:`DEFAULT_LANG` on anything unrecognised so a
    crafted ``?lang=`` query string can't break the page.
    """
    if candidate and candidate in SUPPORTED_LANGS:
        return candidate
    return DEFAULT_LANG


__all__ = [
    "DEFAULT_LANG",
    "STYLE_CARDS",
    "SUPPORTED_LANGS",
    "StyleCard",
    "accepted_style_values",
    "resolve_lang",
]
