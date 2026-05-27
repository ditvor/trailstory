"""Shared test fixtures and dev helpers.

Two roles:

1. Pytest auto-discovers this file when collecting tests. Pytest fixtures
   defined here would be available to every test module — currently none,
   since each test file defines what it needs locally.
2. ``make test-render`` imports :func:`render_with_fixtures` to preview
   the HTML template against bundled fixtures without paying for an LLM
   call. Useful when iterating on the template or CSS.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from trailstory.gpx import parse_gpx
from trailstory.models import (
    CHAPTER_COUNT,
    Chapter,
    HikeInput,
    LocalizedString,
    Memory,
    NarrativeOutput,
    Paragraph,
    Provenance,
    ProvenanceSource,
    Sentence,
    Style,
)
from trailstory.photos import load_photos
from trailstory.renderers.html import render_html

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_GPX = FIXTURES_DIR / "sample.gpx"
SAMPLE_PHOTOS = FIXTURES_DIR / "sample_photos"


def paragraphs_dict_from_strings(
    *,
    en: list[str],
    ru: list[str],
    de: list[str],
    provenance: str = "seed",
    reference: str = "test fixture",
) -> list[list[dict[str, object]]]:
    """Build the dict-literal version of paragraphs for JSON fixtures.

    Many tests construct a fake LLM response as a Python dict that
    serialises to JSON the writer is expected to produce. Those dicts
    don't go through Pydantic until the orchestrator validates them, so
    the helper emits the raw nested-dict shape rather than Sentence
    objects.

    Under ADR-015 the writer's top-level shape is ``chapters``, not
    ``paragraphs`` — see :func:`chapters_dict_from_strings` for the
    six-chapter helper. This helper still exists because the same flat
    shape is reused inside a single chapter's ``body``.
    """
    if len(en) != len(ru) or len(en) != len(de):
        raise ValueError(
            f"paragraphs_dict_from_strings: length mismatch en={len(en)} ru={len(ru)} de={len(de)}"
        )
    return [
        [
            {
                "text": {"en": e, "ru": r, "de": d},
                "provenance": {"source": provenance, "reference": reference},
            }
        ]
        for e, r, d in zip(en, ru, de, strict=True)
    ]


def paragraphs_from_strings(
    *,
    en: list[str],
    ru: list[str],
    de: list[str],
    provenance: ProvenanceSource = ProvenanceSource.SEED,
    reference: str = "test fixture",
) -> list[Paragraph]:
    """Build sentence-level paragraphs from flat per-language lists.

    Each input list is one string per paragraph; the helper produces one
    paragraph per index, each paragraph holding a single Sentence whose
    text carries the en/ru/de triple and whose provenance is
    ``(provenance, reference)``.

    Under ADR-015 ``NarrativeOutput`` does not carry a flat paragraphs
    field anymore (chapters do). This helper survives as the building
    block for :func:`chapters_from_strings` and for any test that needs
    a one-off ``list[Paragraph]`` — e.g. to drop into a single
    ``Chapter.body``. The three input lists must be the same length.
    """
    if len(en) != len(ru) or len(en) != len(de):
        raise ValueError(
            f"paragraphs_from_strings: length mismatch en={len(en)} ru={len(ru)} de={len(de)}"
        )
    out: list[Paragraph] = []
    for e, r, d in zip(en, ru, de, strict=True):
        out.append(
            [
                Sentence(
                    text=LocalizedString(en=e, ru=r, de=d),
                    provenance=Provenance(source=provenance, reference=reference),
                )
            ]
        )
    return out


# Default chapter metadata used by ``chapters_from_strings`` /
# ``chapters_dict_from_strings`` when callers don't supply their own.
# Six entries to match :data:`trailstory.models.CHAPTER_COUNT`; place
# names are plain ASCII so they pass the rubric's Cyrillic check
# defensively (RU/DE variants in the helpers below carry the same
# strings — fixtures don't need locale-correct toponyms).
_DEFAULT_CHAPTER_IDS: tuple[str, ...] = (
    "arrival",
    "ascent",
    "ridge",
    "summit",
    "rest",
    "descent",
)
_DEFAULT_CHAPTER_TIMES: tuple[str, ...] = (
    "08:00",
    "09:30",
    "11:00",
    "12:30",
    "14:00",
    "15:30",
)
_DEFAULT_CHAPTER_TITLES_EN: tuple[str, ...] = (
    "Arrival",
    "Ascent",
    "Ridge",
    "Summit",
    "Rest",
    "Descent",
)
_DEFAULT_CHAPTER_TITLES_RU: tuple[str, ...] = (
    "Приезд",
    "Подъём",
    "Хребет",
    "Вершина",
    "Привал",
    "Спуск",
)
_DEFAULT_CHAPTER_TITLES_DE: tuple[str, ...] = (
    "Ankunft",
    "Aufstieg",
    "Grat",
    "Gipfel",
    "Rast",
    "Abstieg",
)


def chapters_from_strings(
    *,
    en: list[str],
    ru: list[str],
    de: list[str],
    photo_indices: list[int] | None = None,
    place_en: str = "Test Place",
    place_ru: str = "Тестовое место",
    place_de: str = "Testort",
    provenance: ProvenanceSource = ProvenanceSource.SEED,
    reference: str = "test fixture",
) -> list[Chapter]:
    """Build six ADR-015 chapters from flat per-language sentence lists.

    Each input list is one string per chapter (six entries each); each
    chapter's ``body`` becomes a single ``Sentence`` carrying the
    en/ru/de text. Default ids, times, titles, and place strings are
    supplied so callers only need to focus on the body text the test
    actually cares about; ``photo_indices`` defaults to ``[0..5]``
    (matching the typical 6-12-photo fixture range).

    Raises ``ValueError`` if any input list is not exactly
    :data:`CHAPTER_COUNT` entries long or if the three sentence lists
    don't share the same length.
    """
    if len(en) != CHAPTER_COUNT or len(ru) != CHAPTER_COUNT or len(de) != CHAPTER_COUNT:
        raise ValueError(
            f"chapters_from_strings: each language list must have exactly "
            f"{CHAPTER_COUNT} entries (got en={len(en)} ru={len(ru)} de={len(de)})"
        )
    photo_indices = photo_indices if photo_indices is not None else list(range(CHAPTER_COUNT))
    if len(photo_indices) != CHAPTER_COUNT:
        raise ValueError(
            f"chapters_from_strings: photo_indices must have exactly "
            f"{CHAPTER_COUNT} entries (got {len(photo_indices)})"
        )
    place = LocalizedString(en=place_en, ru=place_ru, de=place_de)
    chapters: list[Chapter] = []
    for i in range(CHAPTER_COUNT):
        chapters.append(
            Chapter(
                id=_DEFAULT_CHAPTER_IDS[i],
                time=_DEFAULT_CHAPTER_TIMES[i],
                place=place,
                lat=47.5 + i * 0.001,
                lon=11.7 + i * 0.001,
                title=LocalizedString(
                    en=_DEFAULT_CHAPTER_TITLES_EN[i],
                    ru=_DEFAULT_CHAPTER_TITLES_RU[i],
                    de=_DEFAULT_CHAPTER_TITLES_DE[i],
                ),
                body=[
                    Sentence(
                        text=LocalizedString(en=en[i], ru=ru[i], de=de[i]),
                        provenance=Provenance(source=provenance, reference=reference),
                    )
                ],
                photo_index=photo_indices[i],
            )
        )
    return chapters


def chapters_dict_from_strings(
    *,
    en: list[str],
    ru: list[str],
    de: list[str],
    photo_indices: list[int] | None = None,
    place_en: str = "Test Place",
    place_ru: str = "Тестовое место",
    place_de: str = "Testort",
    provenance: str = "seed",
    reference: str = "test fixture",
) -> list[dict[str, object]]:
    """Dict-literal version of :func:`chapters_from_strings`.

    Mirrors the JSON shape an LLM writer is expected to produce — used
    by tests that build a fake response string the orchestrator validates
    via :func:`NarrativeOutput.model_validate`.
    """
    if len(en) != CHAPTER_COUNT or len(ru) != CHAPTER_COUNT or len(de) != CHAPTER_COUNT:
        raise ValueError(
            f"chapters_dict_from_strings: each language list must have exactly "
            f"{CHAPTER_COUNT} entries (got en={len(en)} ru={len(ru)} de={len(de)})"
        )
    photo_indices = photo_indices if photo_indices is not None else list(range(CHAPTER_COUNT))
    if len(photo_indices) != CHAPTER_COUNT:
        raise ValueError(
            f"chapters_dict_from_strings: photo_indices must have exactly "
            f"{CHAPTER_COUNT} entries (got {len(photo_indices)})"
        )
    return [
        {
            "id": _DEFAULT_CHAPTER_IDS[i],
            "time": _DEFAULT_CHAPTER_TIMES[i],
            "place": {"en": place_en, "ru": place_ru, "de": place_de},
            "lat": 47.5 + i * 0.001,
            "lon": 11.7 + i * 0.001,
            "title": {
                "en": _DEFAULT_CHAPTER_TITLES_EN[i],
                "ru": _DEFAULT_CHAPTER_TITLES_RU[i],
                "de": _DEFAULT_CHAPTER_TITLES_DE[i],
            },
            "body": [
                {
                    "text": {"en": en[i], "ru": ru[i], "de": de[i]},
                    "provenance": {"source": provenance, "reference": reference},
                }
            ],
            "photo_index": photo_indices[i],
        }
        for i in range(CHAPTER_COUNT)
    ]


def sample_narrative() -> NarrativeOutput:
    """Hand-built NarrativeOutput in the same shape an LLM would return."""
    return NarrativeOutput(
        schema_version=4,
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
        chapters=chapters_from_strings(
            en=[
                "We left the trailhead at first light, the air sharp with damp moss.",
                "The forest closed in around us; pine needles softened every step.",
                "By the saddle the cloud was thinning into a soft white scarf.",
                "Mia slept the whole climb, her cheek warm against the carrier.",
                "At the ridge the fog cleared and the valley opened beneath us.",
                "We came down slowly, the light golden on the meadow grass.",
            ],
            ru=[
                "Вышли на тропу с первыми лучами; воздух пах мхом и хвоей.",  # noqa: RUF001
                "Лес сомкнулся вокруг нас; хвоя смягчала каждый шаг.",
                "К седловине облака уже редели, превращаясь в белый шарф.",  # noqa: RUF001
                "Мия проспала весь подъём, прижавшись щекой к переноске.",
                "На хребте туман рассеялся, и долина раскинулась под нами.",  # noqa: RUF001
                "Мы спускались медленно, свет золотил траву на лугу.",
            ],
            de=[
                "Bei erstem Licht brachen wir auf, die Luft scharf von feuchtem Moos.",
                "Der Wald schloss sich um uns; Kiefernnadeln dämpften jeden Schritt.",
                "Am Sattel zog die Wolke sich zu einem weichen weißen Schal zusammen.",
                "Mia schlief den ganzen Aufstieg, die Wange warm an der Trage.",
                "Am Grat lichtete sich der Nebel und das Tal öffnete sich unter uns.",
                "Wir stiegen langsam ab, das Licht golden auf dem Wiesengras.",
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
    )


def render_with_fixtures(
    output_dir: Path | None = None,
    *,
    style: Style = Style.editorial,
) -> Path:
    """Render the HTML template against bundled fixtures (no LLM call).

    Used by ``make test-render`` to iterate on the editorial style
    template or its embedded CSS without making a paid LLM call. The
    output file is named ``test-render-<style>.html``. Returns the
    absolute path of the file that was written.

    ADR-015: ``memory.selected_photos`` mirrors the chapter binding —
    one photo per chapter, in chapter order. This helper rebuilds the
    list from ``narrative.chapters[*].photo_index`` so it stays
    consistent with the production CLI / web pipeline.
    """
    target_dir = output_dir or Path("output/test")
    stats = parse_gpx(SAMPLE_GPX)
    with TemporaryDirectory(prefix="trailstory-test-render-") as tmp:
        photos = load_photos(SAMPLE_PHOTOS, Path(tmp))
        narrative = sample_narrative()
        selected = [photos[c.photo_index] for c in narrative.chapters]
        memory = Memory(
            hike_input=HikeInput(
                gpx_path=SAMPLE_GPX,
                photos_dir=SAMPLE_PHOTOS,
                seed_text="The fog cleared just as we reached the ridge.",
                location_name="Bavarian Alps",
            ),
            gpx_stats=stats,
            narrative=narrative,
            selected_photos=selected,
            style=style,
        )
        out = render_html(
            memory=memory,
            output_dir=target_dir,
            slug=f"test-render-{style.value}",
            hike_date=date(2025, 8, 15),
            location="Bavarian Alps",
        )
    print(f"rendered → {out.resolve()}")
    return out
