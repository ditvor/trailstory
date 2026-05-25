"""Tests for the visual-style switch (ADR-006).

The same ``Memory`` rendered under each :class:`trailstory.models.Style`
must produce structurally distinct HTML — but the narrative text must be
byte-identical across all three. These tests pin both halves of that
contract.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PIL import Image

from tests.conftest import paragraphs_from_strings
from trailstory.models import (
    GpxStats,
    HikeInput,
    LocalizedString,
    Memory,
    NarrativeOutput,
    PhotoMeta,
    Style,
    Waypoint,
)
from trailstory.renderers.html import render_html

ALL_STYLES: tuple[Style, ...] = (Style.editorial, Style.log, Style.encyclopedia)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _gpx_stats() -> GpxStats:
    return GpxStats(
        distance_km=6.2,
        elevation_gain_m=610,
        duration_min=165,
        start_elev_m=720.0,
        summit_elev_m=1330.0,
        waypoints=[
            Waypoint(lat=47.55, lon=11.78, ele_m=720.0, time=None),
            Waypoint(lat=47.555, lon=11.785, ele_m=1100.0, time=None),
            Waypoint(lat=47.56, lon=11.79, ele_m=1330.0, time=None),
        ],
    )


def _narrative() -> NarrativeOutput:
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
                "We left the trailhead at first light.",
                "By the saddle the cloud was thinning.",
            ],
            ru=[
                "Вышли на тропу с первыми лучами.",  # noqa: RUF001
                "К седловине облака начали редеть.",  # noqa: RUF001
            ],
            de=[
                "Bei erstem Licht brachen wir auf.",
                "Am Sattel begann die Wolke sich zu lichten.",
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
        selected_photo_indices=[0, 1, 2],
    )


def _make_photo(tmp_path: Path, idx: int, color: tuple[int, int, int]) -> PhotoMeta:
    p = tmp_path / f"photo_{idx}.jpg"
    Image.new("RGB", (24, 24), color).save(p, "JPEG")
    return PhotoMeta(
        path=p,
        timestamp=datetime(2025, 8, 15, 9 + idx),
        index=idx,
    )


def _memory(photos: list[PhotoMeta], *, style: Style) -> Memory:
    return Memory(
        hike_input=HikeInput(
            gpx_path=Path("/fixtures/sample.gpx"),
            photos_dir=Path("/fixtures/sample_photos"),
            seed_text="The fog cleared just as we reached the ridge.",
            location_name="Bavarian Alps",
        ),
        gpx_stats=_gpx_stats(),
        narrative=_narrative(),
        selected_photos=photos,
        style=style,
    )


def _render_all_styles(tmp_path: Path) -> dict[Style, str]:
    photos = [_make_photo(tmp_path, i, (i * 40, 100, 100)) for i in range(3)]
    rendered: dict[Style, str] = {}
    for style in ALL_STYLES:
        out_path = render_html(
            memory=_memory(photos, style=style),
            output_dir=tmp_path / style.value,
            slug=f"hike-{style.value}",
        )
        rendered[style] = out_path.read_text(encoding="utf-8")
    return rendered


# ── style markers ────────────────────────────────────────────────────────────


def test_each_style_emits_its_own_body_marker_class(tmp_path: Path) -> None:
    """Each rendered output must carry a ``style-<name>`` body class so
    downstream consumers (and these tests) can tell them apart."""
    rendered = _render_all_styles(tmp_path)

    assert 'class="lang-en style-editorial"' in rendered[Style.editorial]
    assert 'class="lang-en style-log"' in rendered[Style.log]
    assert 'class="lang-en style-encyclopedia"' in rendered[Style.encyclopedia]


def test_no_style_class_leaks_into_other_styles(tmp_path: Path) -> None:
    """A render under one style must not contain another style's marker —
    otherwise tests above would pass even if the include path was wrong."""
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        for other in ALL_STYLES:
            if other is style:
                continue
            assert f"style-{other.value}" not in html, (
                f"{style.value} render leaked the {other.value} body class"
            )


def test_log_style_is_list_driven_with_figcaptions(tmp_path: Path) -> None:
    """Log style ships photos as ``<figure>`` entries with captions and uses
    a ``<ul class="stats">`` rather than a CSS grid."""
    html = _render_all_styles(tmp_path)[Style.log]

    assert "<figcaption>" in html
    assert '<ul class="stats"' in html
    assert "Log entry" in html


def test_encyclopedia_style_uses_two_column_body_with_drop_cap(
    tmp_path: Path,
) -> None:
    """Encyclopedia style sets up the kunstbuch register: two-column flow,
    figure captions, and a labelled plate frontispiece."""
    html = _render_all_styles(tmp_path)[Style.encyclopedia]

    assert "column-count: 2" in html
    assert "::first-letter" in html
    assert "Plate" in html  # frontispiece label
    assert "<figcaption>" in html


def test_editorial_style_keeps_magazine_visual_identity(tmp_path: Path) -> None:
    """Editorial is the magazine treatment (Source Serif 4 + JetBrains Mono,
    oklch paper/ink tokens, italic-top / roman-bottom display title, drop
    cap, marginalia sidebar, reading-progress bar). The markers below
    are the load-bearing structural signals — if any of them disappear the
    style has drifted away from the intended design."""
    html = _render_all_styles(tmp_path)[Style.editorial]

    # Embedded WOFF2 fonts (ADR-001 self-contained guarantee).
    assert "@font-face" in html
    assert "Editorial Serif" in html
    assert "Editorial Mono" in html
    assert "data:font/woff2;base64," in html

    # Design tokens and layout primitives.
    assert "--paper:" in html and "--ink:" in html
    assert 'class="display"' in html
    assert 'class="eyebrow"' in html
    assert 'class="margin"' in html
    assert 'class="quote' in html  # the pull-quote callout (may carry extra classes)
    assert 'class="elevation"' in html

    # Three discrete language buttons (replaces the older single EN·RU·DE label).
    assert 'data-lang="en"' in html
    assert 'data-lang="ru"' in html
    assert 'data-lang="de"' in html

    # Editorial does NOT use figcaptions; that is a log/encyclopedia marker.
    assert "<figcaption>" not in html


# ── shared narrative invariants ─────────────────────────────────────────────


def test_narrative_text_is_identical_across_all_styles(tmp_path: Path) -> None:
    """ADR-006: one prompt, three visual treatments. Every user-facing
    narrative string must appear verbatim in each of the three renders."""
    rendered = _render_all_styles(tmp_path)
    narrative = _narrative()

    user_facing_strings = [
        narrative.title.en,
        narrative.title.ru,
        narrative.title.de,
        narrative.subtitle.en,
        narrative.subtitle.ru,
        narrative.subtitle.de,
        narrative.pull_quote.en,
        narrative.pull_quote.ru,
        narrative.pull_quote.de,
        narrative.milestone.en,
        narrative.milestone.ru,
        narrative.milestone.de,
        # ADR-014: paragraphs is now list[Paragraph]; flatten via the
        # helper to get back the per-language strings each style template
        # actually renders.
        *narrative.paragraphs_as_localized().en,
        *narrative.paragraphs_as_localized().ru,
        *narrative.paragraphs_as_localized().de,
    ]
    for style, html in rendered.items():
        for s in user_facing_strings:
            assert s in html, f"missing {s!r} in {style.value} render"


def test_every_style_is_self_contained_no_external_resources(
    tmp_path: Path,
) -> None:
    """The base64-embedding contract (ADR-001) must hold across all styles —
    not just the original editorial one."""
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        assert not re.search(r"<script\s+[^>]*src=", html, flags=re.IGNORECASE), style
        assert not re.search(r'<link\s+[^>]*href=["\']https?://', html, flags=re.IGNORECASE), style
        img_srcs = re.findall(r'<img\s+[^>]*src=["\']([^"\']+)', html, flags=re.IGNORECASE)
        assert img_srcs, f"{style.value}: expected at least one <img>"
        assert all(src.startswith("data:image/") for src in img_srcs), style


def test_every_style_embeds_every_selected_photo(tmp_path: Path) -> None:
    """The photo block is structurally different per style, but the count
    must always match ``len(selected_photos)``."""
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        assert html.count("data:image/jpeg;base64,") == 3, style


def test_every_style_includes_gpx_stats(tmp_path: Path) -> None:
    """Stats land in different markup per style (grid vs list), but every
    headline number must show up somewhere in the body."""
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        assert "6.2" in html, style  # distance_km
        assert "610" in html, style  # elevation_gain_m
        assert "165" in html, style  # duration_min
        assert "1330" in html, style  # summit_elev_m


def test_every_style_emits_inline_elevation_svg(tmp_path: Path) -> None:
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        assert '<svg class="elevation"' in html, style
        assert "<path " in html, style
