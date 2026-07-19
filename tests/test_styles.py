"""Tests for the visual-style switch (ADR-006, ADR-021).

The same ``Memory`` rendered under each built :class:`trailstory.models.Style`
must produce structurally distinct HTML — but the narrative text must be
byte-identical across styles. These tests pin both halves of that
contract, plus the ADR-021 lineup rules: ``letter``, ``zine``, and
``postcard`` are the built styles today, and the planned styles
(sunday / album) are refused by the renderer until their templates land.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import paragraphs_from_strings
from trailstory.models import (
    BUILT_STYLES,
    GpxStats,
    HikeInput,
    LocalizedString,
    Memory,
    NarrativeOutput,
    PhotoMeta,
    Style,
    Waypoint,
)
from trailstory.renderers.html import HtmlRenderError, render_html

ALL_BUILT_STYLES: tuple[Style, ...] = tuple(sorted(BUILT_STYLES))

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
        schema_version=5,
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
    for style in ALL_BUILT_STYLES:
        out_path = render_html(
            memory=_memory(photos, style=style),
            output_dir=tmp_path / style.value,
            slug=f"hike-{style.value}",
        )
        rendered[style] = out_path.read_text(encoding="utf-8")
    return rendered


# ── ADR-021 lineup rules ─────────────────────────────────────────────────────


def test_built_styles_is_a_subset_of_the_lineup() -> None:
    """Every built style must be a real enum member; ``letter``,
    ``zine``, and ``postcard`` are the ones built today."""
    assert BUILT_STYLES <= frozenset(Style)
    assert BUILT_STYLES == frozenset({Style.letter, Style.zine, Style.postcard})


def test_planned_styles_are_in_the_enum_but_not_built() -> None:
    """The two planned styles (see the web picker's SOON cards) exist
    as enum members so the pipeline vocabulary is ready, but have no
    renderer yet."""
    planned = {Style.sunday, Style.album}
    assert planned <= set(Style)
    assert planned.isdisjoint(BUILT_STYLES)


@pytest.mark.parametrize("style", sorted(set(Style) - BUILT_STYLES))
def test_rendering_an_unbuilt_style_raises_a_clear_error(tmp_path: Path, style: Style) -> None:
    photos = [_make_photo(tmp_path, i, (i * 40, 100, 100)) for i in range(3)]
    with pytest.raises(HtmlRenderError, match="no renderer template yet"):
        render_html(
            memory=_memory(photos, style=style),
            output_dir=tmp_path,
            slug="hike-unbuilt",
        )


# ── style markers ────────────────────────────────────────────────────────────


def test_each_style_emits_its_own_body_marker_class(tmp_path: Path) -> None:
    """Each rendered output must carry a ``style-<name>`` body class so
    downstream consumers (and these tests) can tell them apart."""
    rendered = _render_all_styles(tmp_path)

    assert 'class="lang-en style-letter"' in rendered[Style.letter]
    assert 'class="lang-en style-zine"' in rendered[Style.zine]
    assert 'class="lang-en style-postcard"' in rendered[Style.postcard]


def test_no_style_class_leaks_into_other_styles(tmp_path: Path) -> None:
    """A render under one style must not contain another style's marker —
    otherwise tests above would pass even if the include path was wrong."""
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        for other in Style:
            if other is style:
                continue
            assert f"style-{other.value}" not in html, (
                f"{style.value} render leaked the {other.value} body class"
            )


def test_letter_style_keeps_magazine_visual_identity(tmp_path: Path) -> None:
    """The Letter is the editorial magazine treatment (Source Serif 4 +
    JetBrains Mono, oklch paper/ink tokens, italic-top / roman-bottom
    display title, drop cap, marginalia sidebar, reading-progress bar).
    The markers below are the load-bearing structural signals — if any of
    them disappear the style has drifted away from the intended design."""
    html = _render_all_styles(tmp_path)[Style.letter]

    # Embedded WOFF2 fonts (ADR-001 self-contained guarantee).
    assert "@font-face" in html
    assert "Letter Serif" in html
    assert "Letter Mono" in html
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

    # The Letter does NOT use figcaptions; photos flow inside the prose.
    assert "<figcaption>" not in html


def test_postcard_style_keeps_travel_card_identity(tmp_path: Path) -> None:
    """Postcard Set is the mid-century travel-card treatment (Yeseva One
    display + Caveat handwriting, postal red / airmail blue tokens, one
    front/back card pair per paragraph with stamp, postmark, and address
    block). The markers below are the load-bearing structural signals —
    if any of them disappear the style has drifted away from the picker
    card's promise ("front and back, with stamp, postmark, and an
    address line")."""
    html = _render_all_styles(tmp_path)[Style.postcard]

    # Embedded WOFF2 fonts (ADR-001 self-contained guarantee).
    assert "@font-face" in html
    assert "Postcard Display" in html
    assert "Postcard Hand" in html
    assert "Postcard Mono" in html
    assert "data:font/woff2;base64," in html

    # Design tokens and the postcard anatomy.
    assert "--pc-red:" in html and "--pc-blue:" in html
    assert 'class="card front"' in html
    assert 'class="card back plain"' in html
    assert 'class="stamp"' in html
    assert 'class="postmark"' in html
    assert 'class="addr"' in html
    assert "TRAILSTORY" in html  # the stamp's issue text

    # Three discrete language buttons.
    assert 'data-lang="en"' in html
    assert 'data-lang="ru"' in html
    assert 'data-lang="de"' in html


# ── shared narrative invariants ─────────────────────────────────────────────


def test_narrative_text_is_identical_across_all_styles(tmp_path: Path) -> None:
    """ADR-006: one prompt, N visual treatments. Every user-facing
    narrative string must appear verbatim in each built style's render."""
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
    """The base64-embedding contract (ADR-001) must hold across all built
    styles — not just The Letter."""
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
    """Stats land in different markup per style, but every headline
    number must show up somewhere in the body."""
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        assert "6.2" in html, style  # distance_km
        assert "610" in html, style  # elevation_gain_m
        # duration_min=165 rendered through the shared '%dh %02dm' idiom.
        # (The raw "165" this test used to look for only ever matched by
        # accident inside base64 font payloads.)
        assert "2h 45m" in html, style
        assert "1330" in html, style  # summit_elev_m


def test_every_style_emits_inline_elevation_svg(tmp_path: Path) -> None:
    rendered = _render_all_styles(tmp_path)
    for style, html in rendered.items():
        assert '<svg class="elevation"' in html, style
        assert "<path " in html, style
