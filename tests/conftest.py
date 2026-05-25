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
    """Build the dict-literal version of ADR-014 paragraphs for JSON fixtures.

    Many tests construct a fake LLM response as a Python dict that
    serialises to JSON the writer is expected to produce. Those dicts
    don't go through Pydantic until the orchestrator validates them, so
    the helper emits the raw nested-dict shape rather than Sentence
    objects. Use :func:`paragraphs_from_strings` for tests that build
    fully-validated :class:`NarrativeOutput` instances directly.
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
    """Build ADR-014 sentence-level paragraphs from flat per-language lists.

    Each input list is one string per paragraph; the helper produces one
    paragraph per index, each paragraph holding a single Sentence whose
    text carries the en/ru/de triple and whose provenance is
    ``(provenance, reference)``. Tests use this to keep their old
    string-based intent without having to hand-build sentence objects.

    The three input lists must be the same length; the helper does not
    silently align mismatched lengths because that's a bug, not a
    feature.
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


def sample_narrative() -> NarrativeOutput:
    """Hand-built NarrativeOutput in the same shape an LLM would return."""
    return NarrativeOutput(
        schema_version=3,
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
        paragraphs=paragraphs_from_strings(
            en=[
                "We left the trailhead at first light, the air sharp with damp moss.",
                "By the saddle the cloud was thinning into a soft white scarf.",
                "Mia slept the whole climb, her cheek warm against the carrier.",
            ],
            ru=[
                "Вышли на тропу с первыми лучами; воздух пах мхом и хвоей.",  # noqa: RUF001
                "К седловине облака уже редели, превращаясь в белый шарф.",  # noqa: RUF001
                "Мия проспала весь подъём, прижавшись щекой к переноске.",
            ],
            de=[
                "Bei erstem Licht brachen wir auf, die Luft scharf von feuchtem Moos.",
                "Am Sattel zog die Wolke sich zu einem weichen weißen Schal zusammen.",
                "Mia schlief den ganzen Aufstieg, die Wange warm an der Trage.",
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
        selected_photo_indices=[0, 1, 2, 3, 4],
    )


def render_with_fixtures(
    output_dir: Path | None = None,
    *,
    style: Style = Style.editorial,
) -> Path:
    """Render the HTML template against bundled fixtures (no LLM call).

    Used by ``make test-render`` to iterate on the style templates or
    their embedded CSS without making a paid LLM call. The output file is
    named ``test-render-<style>.html`` so all three styles can land in
    the same directory side by side. Returns the absolute path of the
    file that was written.
    """
    target_dir = output_dir or Path("output/test")
    stats = parse_gpx(SAMPLE_GPX)
    with TemporaryDirectory(prefix="trailstory-test-render-") as tmp:
        photos = load_photos(SAMPLE_PHOTOS, Path(tmp))
        narrative = sample_narrative()
        memory = Memory(
            hike_input=HikeInput(
                gpx_path=SAMPLE_GPX,
                photos_dir=SAMPLE_PHOTOS,
                seed_text="The fog cleared just as we reached the ridge.",
                location_name="Bavarian Alps",
            ),
            gpx_stats=stats,
            narrative=narrative,
            selected_photos=photos,
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
