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
from trailstory.models import HikeInput, Memory, NarrativeOutput
from trailstory.photos import load_photos
from trailstory.renderers.html import render_html

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_GPX = FIXTURES_DIR / "sample.gpx"
SAMPLE_PHOTOS = FIXTURES_DIR / "sample_photos"


def sample_narrative() -> NarrativeOutput:
    """Hand-built NarrativeOutput in the same shape an LLM would return."""
    return NarrativeOutput(
        schema_version=1,
        title_en="Above the fog line",
        title_ru="Над линией тумана",
        subtitle_en="A morning above the cloud sea",
        subtitle_ru="Утро над морем облаков",
        paragraphs_en=[
            "We left the trailhead at first light, the air sharp with damp moss.",
            "By the saddle the cloud was thinning into a soft white scarf.",
            "Mia slept the whole climb, her cheek warm against the carrier.",
        ],
        paragraphs_ru=[
            "Вышли на тропу с первыми лучами; воздух пах мхом и хвоей.",  # noqa: RUF001
            "К седловине облака уже редели, превращаясь в белый шарф.",  # noqa: RUF001
            "Мия проспала весь подъём, прижавшись щекой к переноске.",
        ],
        pull_quote_en="The fog cleared just as we reached the ridge.",
        pull_quote_ru="Туман рассеялся как раз когда мы вышли на хребет.",
        milestone_en="First mountain hike",
        milestone_ru="Первый горный поход",
        selected_photo_indices=[0, 1, 2, 3, 4],
    )


def render_with_fixtures(output_dir: Path | None = None) -> Path:
    """Render the HTML template against bundled fixtures (no LLM call).

    Used by ``make test-render`` to iterate on
    ``templates/memory.html.j2`` or its embedded CSS without making a paid
    LLM call. Returns the absolute path of the file that was written.
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
                baby_name="Mia",
                baby_age_months=5,
                location_name="Bavarian Alps",
            ),
            gpx_stats=stats,
            narrative=narrative,
            selected_photos=photos,
        )
        out = render_html(
            memory=memory,
            output_dir=target_dir,
            slug="test-render",
            hike_date=date(2025, 8, 15),
            location="Bavarian Alps",
        )
    print(f"rendered → {out.resolve()}")
    return out
