"""Tests for ``trailstory.renderers.html``.

Per CLAUDE.md, these test the contract — what the rendered HTML contains
and where it lands — not the Jinja2 internals. No network, no LLM: photos
are tiny JPEGs created on the fly with Pillow.
"""

from __future__ import annotations

import base64
import io
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from tests.conftest import paragraphs_from_strings
from trailstory.models import (
    GpxStats,
    HikeInput,
    LocalizedString,
    Memory,
    NarrativeOutput,
    Paragraph,
    PhotoMeta,
    PlaceContext,
    Style,
    Waypoint,
)
from trailstory.photos import load_photos
from trailstory.renderers.html import HtmlRenderError, render_html

EXIF_SUB_IFD = 0x8769
EXIF_GPS_IFD = 0x8825
EXIF_DATETIME_ORIGINAL = 36867

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


def _memory(
    photos: list[PhotoMeta],
    *,
    narrative: NarrativeOutput | None = None,
    gpx_stats: GpxStats | None = None,
    place_context: PlaceContext | None = None,
    style: Style = Style.editorial,
) -> Memory:
    """Build a ``Memory`` from a photo list plus optional narrative / stats overrides."""
    return Memory(
        hike_input=HikeInput(
            gpx_path=Path("/fixtures/sample.gpx"),
            photos_dir=Path("/fixtures/sample_photos"),
            seed_text="The fog cleared just as we reached the ridge.",
            location_name="Bavarian Alps",
        ),
        gpx_stats=gpx_stats if gpx_stats is not None else _gpx_stats(),
        narrative=narrative if narrative is not None else _narrative(),
        selected_photos=photos,
        place_context=place_context,
        style=style,
    )


def _place_context() -> PlaceContext:
    return PlaceContext(
        town="Bad Tölz",
        region="Bavarian Prealps",
        summary=LocalizedString(
            en="Bad Tölz is a spa town on the Isar in the Bavarian Prealps.",
            ru="Бад-Тёльц — курортный город на реке Изар.",
            de="Bad Tölz ist ein Kurort an der Isar in den Bayerischen Voralpen.",
        ),
        source_url="https://en.wikipedia.org/wiki/Bad_Tölz",
        source_title="Bad Tölz",
    )


def _flat_string(en: str = "x", ru: str = "x", de: str = "x") -> LocalizedString:
    return LocalizedString(en=en, ru=ru, de=de)


def _flat_paragraphs(
    *, en: list[str] | None = None, ru: list[str] | None = None, de: list[str] | None = None
) -> list[Paragraph]:
    return paragraphs_from_strings(
        en=en if en is not None else ["x"],
        ru=ru if ru is not None else ["x"],
        de=de if de is not None else ["x"],
    )


# ── tests ────────────────────────────────────────────────────────────────────


def test_render_writes_html_at_expected_path(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, i, (i * 40, 100, 100)) for i in range(3)]
    out_dir = tmp_path / "out"

    out_path = render_html(
        memory=_memory(photos),
        output_dir=out_dir,
        slug="2025-08-15-zugspitze",
        hike_date=date(2025, 8, 15),
        location="Bavarian Alps",
    )

    assert out_path == out_dir / "2025-08-15-zugspitze.html"
    assert out_path.is_file()


def test_render_creates_missing_output_directory(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]
    nested = tmp_path / "deeply" / "nested" / "out"

    out_path = render_html(
        memory=_memory(photos),
        output_dir=nested,
        slug="hike",
    )

    assert out_path.is_file()
    assert nested.is_dir()


def test_render_includes_trilingual_narrative_content(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]

    out_path = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    assert "Above the fog line" in html
    assert "Над линией тумана" in html
    assert "Über der Nebelgrenze" in html
    assert "A morning above the cloud sea" in html
    assert "Утро над морем облаков" in html
    assert "Ein Morgen über dem Wolkenmeer" in html
    assert "First mountain hike" in html
    assert "Первый горный поход" in html
    assert "Erste Bergwanderung" in html
    assert "We left the trailhead at first light." in html
    assert "К седловине облака начали редеть." in html  # noqa: RUF001
    assert "Am Sattel begann die Wolke sich zu lichten." in html
    assert "The fog cleared just as we reached the ridge." in html


def test_render_embeds_every_photo_as_jpeg_data_uri(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, i, (i * 40, 100, 100)) for i in range(4)]

    out_path = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    assert html.count("data:image/jpeg;base64,") == 4


def test_render_is_self_contained_no_external_resources(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]

    out_path = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    # No external scripts or stylesheets — everything inline.
    assert not re.search(r"<script\s+[^>]*src=", html, flags=re.IGNORECASE)
    assert not re.search(r'<link\s+[^>]*href=["\']https?://', html, flags=re.IGNORECASE)
    # No images loaded from a remote URL — every <img> must use a data: URI.
    assert not re.search(r'<img\s+[^>]*src=["\']https?://', html, flags=re.IGNORECASE)
    img_srcs = re.findall(r'<img\s+[^>]*src=["\']([^"\']+)', html, flags=re.IGNORECASE)
    assert img_srcs, "expected at least one <img> in output"
    assert all(src.startswith("data:image/") for src in img_srcs)


def test_render_includes_gpx_stats(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]

    out_path = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    assert "6.2" in html  # distance_km
    assert "610" in html  # elevation_gain_m
    assert "165" in html  # duration_min
    assert "1330" in html  # summit_elev_m


def test_render_emits_inline_elevation_svg(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]

    out_path = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    assert '<svg class="elevation"' in html
    assert "<path " in html
    assert "viewBox=" in html


def test_render_escapes_html_in_narrative_fields(tmp_path: Path) -> None:
    """LLM output is untrusted — autoescape must neutralise HTML."""
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]
    nasty = NarrativeOutput(
        schema_version=5,
        title=_flat_string(en="<script>alert(1)</script>"),
        subtitle=_flat_string(),
        paragraphs=_flat_paragraphs(en=["</p><img src=x onerror=alert(1)>"]),
        pull_quote=_flat_string(),
        milestone=_flat_string(),
        selected_photo_indices=[0],
    )

    out_path = render_html(
        memory=_memory(photos, narrative=nasty),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # The `<img onerror>` payload must not appear as a parseable tag.
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_render_escapes_narrative_when_emitted_into_script_block(
    tmp_path: Path,
) -> None:
    """The share-button JS uses ``| tojson``; ``</script>`` must not survive raw."""
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]
    nasty = NarrativeOutput(
        schema_version=5,
        title=_flat_string(en="legit"),
        subtitle=_flat_string(),
        paragraphs=_flat_paragraphs(),
        pull_quote=_flat_string(en="</script><script>alert(1)</script>"),
        milestone=_flat_string(),
        selected_photo_indices=[0],
    )

    out_path = render_html(
        memory=_memory(photos, narrative=nasty),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    # `tojson` must escape `<` so the literal ``</script>`` cannot break out.
    assert "</script><script>alert(1)</script>" not in html


def test_render_raises_when_photos_empty(tmp_path: Path) -> None:
    with pytest.raises(HtmlRenderError, match="at least one photo"):
        render_html(
            memory=_memory([]),
            output_dir=tmp_path / "out",
            slug="hike",
        )


def test_render_raises_when_slug_empty(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]
    with pytest.raises(HtmlRenderError, match="slug"):
        render_html(
            memory=_memory(photos),
            output_dir=tmp_path / "out",
            slug="",
        )


def test_render_raises_when_photo_file_unreadable(tmp_path: Path) -> None:
    missing = PhotoMeta(
        path=tmp_path / "does_not_exist.jpg",
        timestamp=datetime(2025, 8, 15, 9),
        index=0,
    )
    with pytest.raises(HtmlRenderError, match="unable to read photo"):
        render_html(
            memory=_memory([missing]),
            output_dir=tmp_path / "out",
            slug="hike",
        )


def test_render_meta_line_renders_only_when_provided(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]

    without_meta = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "no_meta",
        slug="hike",
    ).read_text(encoding="utf-8")
    with_meta = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "with_meta",
        slug="hike",
        hike_date=date(2025, 8, 15),
        location="Bavarian Alps",
    ).read_text(encoding="utf-8")

    assert 'class="meta"' not in without_meta
    assert "Bavarian Alps" in with_meta
    assert "2025-08-15" in with_meta


def test_render_does_not_embed_gps_exif_after_load_photos(tmp_path: Path) -> None:
    """End-to-end privacy guarantee: a source photo carrying GPS coordinates
    must not produce an embedded data URI with GPS in the rendered HTML.
    Goes through the real ``load_photos → render_html`` pipeline."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_path = src_dir / "with_gps.jpg"

    img = Image.new("RGB", (200, 150), (50, 80, 120))
    exif = img.getexif()
    sub_ifd = exif.get_ifd(EXIF_SUB_IFD)
    sub_ifd[EXIF_DATETIME_ORIGINAL] = "2025:08:15 10:30:00"
    gps_ifd = exif.get_ifd(EXIF_GPS_IFD)
    gps_ifd[1] = "N"
    gps_ifd[2] = (IFDRational(47, 1), IFDRational(33, 1), IFDRational(0, 1))
    gps_ifd[3] = "E"
    gps_ifd[4] = (IFDRational(11, 1), IFDRational(47, 1), IFDRational(0, 1))
    img.save(src_path, format="JPEG", quality=85, exif=exif.tobytes())

    photos = load_photos(src_dir, tmp_path / "resized")

    out_path = render_html(
        memory=_memory(photos),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")

    match = re.search(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", html)
    assert match is not None, "expected an embedded JPEG data URI"
    embedded_bytes = base64.b64decode(match.group(1))

    with Image.open(io.BytesIO(embedded_bytes)) as embedded:
        assert embedded.getexif().get_ifd(EXIF_GPS_IFD) == {}


# ── ADR-017: "about this place" block ────────────────────────────────────────


@pytest.mark.parametrize("style", list(Style))
def test_render_includes_place_block(tmp_path: Path, style: Style) -> None:
    """Every style renders the block, its tri-lingual summary, and the source link."""
    photos = [_make_photo(tmp_path, 0, (200, 80, 80))]
    out_path = render_html(
        memory=_memory(photos, place_context=_place_context(), style=style),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")
    assert 'class="place"' in html
    # Town name appears in the heading regardless of the per-style label.
    assert "Bad Tölz" in html
    # Tri-lingual summary present in every style.
    assert "spa town on the Isar" in html
    assert "Бад-Тёльц — курортный город на реке Изар." in html
    assert "Kurort an der Isar" in html
    # Source attribution link present (URL is HTML-attribute-escaped but the
    # path survives).
    assert 'href="https://en.wikipedia.org/wiki/Bad_T' in html


@pytest.mark.parametrize("style", list(Style))
def test_render_omits_place_block_when_absent(tmp_path: Path, style: Style) -> None:
    """Default render (no place context) carries no place markup in any style."""
    photos = [_make_photo(tmp_path, 0, (200, 80, 80))]
    out_path = render_html(
        memory=_memory(photos, style=style),
        output_dir=tmp_path / "out",
        slug="hike",
    )
    html = out_path.read_text(encoding="utf-8")
    assert 'class="place"' not in html
    assert "spa town on the Isar" not in html
