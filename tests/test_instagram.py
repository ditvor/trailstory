"""Tests for ``trailstory.renderers.instagram``.

Pillow does the heavy lifting; the tests assert structure and dimensions
rather than pixel-perfect rendering. No network, no LLM.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from trailstory.models import NarrativeOutput, PhotoMeta
from trailstory.renderers.instagram import (
    SLIDE_H,
    SLIDE_W,
    InstagramRenderError,
    _wrap_text,
    render_instagram_carousel,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _narrative() -> NarrativeOutput:
    return NarrativeOutput(
        title_en="Above the fog line",
        title_ru="Над линией тумана",
        subtitle_en="A morning above the cloud sea",
        subtitle_ru="Утро над морем облаков",
        paragraphs_en=["First.", "Second."],
        paragraphs_ru=["Первый.", "Второй."],
        pull_quote_en="The fog cleared just as we reached the ridge.",
        pull_quote_ru="Туман рассеялся как раз когда мы вышли на хребет.",
        milestone_en="First mountain hike",
        milestone_ru="Первый горный поход",
        selected_photo_indices=[0, 1, 2],
    )


def _make_photo(
    tmp_path: Path,
    idx: int,
    color: tuple[int, int, int],
    *,
    size: tuple[int, int] = (1800, 1200),
) -> PhotoMeta:
    """Create a JPEG fixture at the given size and return its PhotoMeta."""
    p = tmp_path / f"photo_{idx}.jpg"
    Image.new("RGB", size, color).save(p, "JPEG", quality=85)
    return PhotoMeta(
        path=p,
        timestamp=datetime(2025, 8, 15, 9 + idx),
        index=idx,
    )


# ── happy path ───────────────────────────────────────────────────────────────


def test_carousel_creates_title_photos_and_quote_in_order(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, i, (i * 40, 100, 100)) for i in range(3)]
    out_dir = tmp_path / "out"

    paths = render_instagram_carousel(
        narrative=_narrative(),
        photos=photos,
        output_dir=out_dir,
        slug="2025-08-15-zugspitze",
    )

    assert len(paths) == 5  # 1 title + 3 photos + 1 quote
    assert all(p.is_file() for p in paths)
    assert paths[0].name == "00_title.jpg"
    assert paths[1].name == "01_photo.jpg"
    assert paths[3].name == "03_photo.jpg"
    assert paths[-1].name == "04_quote.jpg"
    # All slides land inside the slug-namespaced carousel/ directory.
    expected_dir = out_dir / "2025-08-15-zugspitze" / "carousel"
    assert all(p.parent == expected_dir for p in paths)


def test_carousel_every_slide_is_1080x1350_jpeg(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, i, (i * 40, 100, 100)) for i in range(2)]

    paths = render_instagram_carousel(
        narrative=_narrative(),
        photos=photos,
        output_dir=tmp_path / "out",
        slug="hike",
    )

    assert paths
    for p in paths:
        with Image.open(p) as img:
            assert img.size == (SLIDE_W, SLIDE_H), p
            assert img.format == "JPEG", p


def test_carousel_center_crops_landscape_photos_without_distortion(
    tmp_path: Path,
) -> None:
    """A 1600x900 source must come out 1080x1350 — center-cropped, never
    stretched."""
    src = tmp_path / "wide.jpg"
    Image.new("RGB", (1600, 900), (200, 100, 50)).save(src, "JPEG")
    photo = PhotoMeta(path=src, timestamp=datetime(2025, 8, 15), index=0)

    paths = render_instagram_carousel(
        narrative=_narrative(),
        photos=[photo],
        output_dir=tmp_path / "out",
        slug="hike",
    )
    # paths[1] is the photo slide (paths[0] is the title).
    with Image.open(paths[1]) as img:
        assert img.size == (SLIDE_W, SLIDE_H)


def test_carousel_handles_portrait_photos(tmp_path: Path) -> None:
    """A 900x1600 source (taller than 4:5) must also fit cleanly."""
    photo = _make_photo(tmp_path, 0, (60, 80, 120), size=(900, 1600))

    paths = render_instagram_carousel(
        narrative=_narrative(),
        photos=[photo],
        output_dir=tmp_path / "out",
        slug="hike",
    )
    with Image.open(paths[1]) as img:
        assert img.size == (SLIDE_W, SLIDE_H)


def test_carousel_creates_nested_output_directory(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]
    nested = tmp_path / "deeply" / "nested"

    paths = render_instagram_carousel(
        narrative=_narrative(),
        photos=photos,
        output_dir=nested,
        slug="hike",
    )

    assert paths[0].parent.is_dir()
    assert paths[0].parent == nested / "hike" / "carousel"


def test_carousel_includes_hike_date_and_location_when_provided(
    tmp_path: Path,
) -> None:
    """Footer rendering doesn't crash and produces a slide of the right size."""
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]

    paths = render_instagram_carousel(
        narrative=_narrative(),
        photos=photos,
        output_dir=tmp_path / "out",
        slug="hike",
        hike_date=date(2025, 8, 15),
        location="Bavarian Alps",
    )

    with Image.open(paths[0]) as img:
        assert img.size == (SLIDE_W, SLIDE_H)


# ── error surface ────────────────────────────────────────────────────────────


def test_carousel_raises_on_empty_photos(tmp_path: Path) -> None:
    with pytest.raises(InstagramRenderError, match="at least one"):
        render_instagram_carousel(
            narrative=_narrative(),
            photos=[],
            output_dir=tmp_path / "out",
            slug="hike",
        )


def test_carousel_raises_on_empty_slug(tmp_path: Path) -> None:
    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]
    with pytest.raises(InstagramRenderError, match="slug"):
        render_instagram_carousel(
            narrative=_narrative(),
            photos=photos,
            output_dir=tmp_path / "out",
            slug="",
        )


def test_carousel_raises_on_unreadable_photo(tmp_path: Path) -> None:
    missing = PhotoMeta(
        path=tmp_path / "does_not_exist.jpg",
        timestamp=datetime(2025, 8, 15),
        index=0,
    )
    with pytest.raises(InstagramRenderError, match="unable to read photo"):
        render_instagram_carousel(
            narrative=_narrative(),
            photos=[missing],
            output_dir=tmp_path / "out",
            slug="hike",
        )


# ── font fallback ────────────────────────────────────────────────────────────


def test_carousel_renders_when_no_system_font_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with no system serif paths, slides must still render via Pillow's
    bundled DejaVu fallback (with a logged warning)."""
    monkeypatch.setattr("trailstory.renderers.instagram.SERIF_PATHS", ())
    monkeypatch.setattr("trailstory.renderers.instagram.SERIF_BOLD_PATHS", ())

    photos = [_make_photo(tmp_path, 0, (50, 80, 120))]
    paths = render_instagram_carousel(
        narrative=_narrative(),
        photos=photos,
        output_dir=tmp_path / "out",
        slug="hike",
    )

    assert len(paths) == 3
    with Image.open(paths[0]) as img:
        assert img.size == (SLIDE_W, SLIDE_H)


# ── text wrapping (unit) ─────────────────────────────────────────────────────


def test_wrap_text_produces_single_line_when_text_fits() -> None:
    font = ImageFont.load_default(size=24)
    lines = _wrap_text("hello", font, max_width=10_000)
    assert lines == ["hello"]


def test_wrap_text_splits_on_word_boundaries_when_overflow() -> None:
    """The wrap actually happens, every word survives, and the split lands on
    a whitespace boundary (no word is cut in half)."""
    font = ImageFont.load_default(size=24)
    text = "hello world this is a deliberately long sentence"
    lines = _wrap_text(text, font, max_width=200)

    assert len(lines) > 1
    # Joining the wrapped lines must reproduce the original word sequence.
    assert " ".join(lines).split() == text.split()
    # No line contains internal newlines or fragments of the input.
    for line in lines:
        assert "\n" not in line


def test_wrap_text_handles_empty_input() -> None:
    font = ImageFont.load_default(size=24)
    assert _wrap_text("", font, max_width=100) == [""]
