"""Instagram carousel renderer.

Generates a sequence of 1080x1350 portrait JPEGs from a ``NarrativeOutput``
and a list of selected photos. Output lands under
``output_dir/{slug}/carousel/`` in display order: a title slide, the photos
center-cropped to 4:5, and a closing pull-quote slide.

The carousel is intentionally **English-only**: Instagram captions are the
right place for the Russian translation, and single-language slides are
more legible at thumbnail size on a phone. The audience for the bilingual
toggle is the HTML page sent to family on WhatsApp / Telegram, not the
public IG post.

Public surface is :func:`render_instagram_carousel`. Helpers are private.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont, ImageOps

from trailstory.models import NarrativeOutput, PhotoMeta

logger = logging.getLogger(__name__)

# ── visual constants ─────────────────────────────────────────────────────────

SLIDE_W: Final[int] = 1080
SLIDE_H: Final[int] = 1350
DEFAULT_JPEG_QUALITY: Final[int] = 90

# Palette mirrors templates/memory.html.j2 so the carousel feels of a piece
# with the HTML page.
BG_COLOR: Final[tuple[int, int, int]] = (250, 248, 244)  # warm cream
INK_COLOR: Final[tuple[int, int, int]] = (26, 26, 26)
ACCENT_COLOR: Final[tuple[int, int, int]] = (107, 90, 44)
SUBTLE_COLOR: Final[tuple[int, int, int]] = (108, 108, 108)

# Font search paths. Pillow's load_default(size=) is used as a last-resort
# fallback so the carousel still renders even on a stripped-down Linux box.
SERIF_PATHS: Final[tuple[str, ...]] = (
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
)
SERIF_BOLD_PATHS: Final[tuple[str, ...]] = (
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/Library/Fonts/Georgia Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
)


class InstagramRenderError(Exception):
    """Raised when the carousel cannot be produced."""


def render_instagram_carousel(
    *,
    narrative: NarrativeOutput,
    photos: list[PhotoMeta],
    output_dir: Path,
    slug: str,
    hike_date: date | None = None,
    location: str | None = None,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> list[Path]:
    """Render the Instagram carousel for one hike.

    Args:
        narrative: Validated bilingual narrative. Only the English fields
            are rendered onto slides.
        photos: Photos to display, already filtered to the LLM's selected
            indices and in display order.
        output_dir: Directory under which ``{slug}/carousel/`` is created.
        slug: URL-safe identifier; matches the HTML output filename.
        hike_date: Optional date shown on the title slide footer.
        location: Optional location shown on the title slide footer.
        quality: JPEG quality (1-95) used when writing each slide. Defaults
            to :data:`DEFAULT_JPEG_QUALITY`; the CLI passes
            ``settings.instagram_quality`` here.

    Returns:
        Paths in display order: title, photos…, quote.

    Raises:
        InstagramRenderError: photos is empty, slug is empty, or a photo
            cannot be opened by Pillow.
    """
    if not photos:
        raise InstagramRenderError("at least one photo is required")
    if not slug:
        raise InstagramRenderError("slug must be a non-empty string")

    target = output_dir / slug / "carousel"
    target.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []

    title_path = target / "00_title.jpg"
    _save_jpeg(_render_title_slide(narrative, hike_date, location), title_path, quality=quality)
    paths.append(title_path)

    for n, photo in enumerate(photos, start=1):
        path = target / f"{n:02d}_photo.jpg"
        _save_jpeg(_render_photo_slide(photo), path, quality=quality)
        paths.append(path)

    quote_path = target / f"{len(photos) + 1:02d}_quote.jpg"
    _save_jpeg(_render_quote_slide(narrative), quote_path, quality=quality)
    paths.append(quote_path)

    return paths


# ── slide composition ────────────────────────────────────────────────────────


def _render_title_slide(
    narrative: NarrativeOutput,
    hike_date: date | None,
    location: str | None,
) -> Image.Image:
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Milestone — letter-spaced uppercase, near the top.
    milestone_font = _load_font(SERIF_BOLD_PATHS, size=34)
    _draw_centered_text(draw, narrative.milestone_en.upper(), milestone_font, ACCENT_COLOR, y=200)

    # Title — wrapped, centered, large bold serif.
    title_font = _load_font(SERIF_BOLD_PATHS, size=88)
    title_lines = _wrap_text(narrative.title_en, title_font, max_width=SLIDE_W - 160)
    title_top = 360
    title_bottom = _draw_centered_block(
        draw, title_lines, title_font, INK_COLOR, top=title_top, line_spacing=18
    )

    # Subtitle — italic-ish (regular serif, smaller, subtle colour).
    subtitle_font = _load_font(SERIF_PATHS, size=42)
    sub_lines = _wrap_text(narrative.subtitle_en, subtitle_font, max_width=SLIDE_W - 200)
    _draw_centered_block(
        draw,
        sub_lines,
        subtitle_font,
        SUBTLE_COLOR,
        top=title_bottom + 40,
        line_spacing=12,
    )

    # Footer — date · location, only if either is provided.
    footer_bits = [b for b in (location, hike_date.isoformat() if hike_date else None) if b]
    if footer_bits:
        footer_font = _load_font(SERIF_PATHS, size=30)
        _draw_centered_text(
            draw, " · ".join(footer_bits), footer_font, SUBTLE_COLOR, y=SLIDE_H - 120
        )

    return img


def _render_photo_slide(photo: PhotoMeta) -> Image.Image:
    try:
        with Image.open(photo.path) as src:
            rgb = src.convert("RGB")
            return ImageOps.fit(
                rgb,
                (SLIDE_W, SLIDE_H),
                method=Image.Resampling.LANCZOS,
            )
    except (OSError, FileNotFoundError) as exc:
        raise InstagramRenderError(f"unable to read photo {photo.path}: {exc}") from exc


def _render_quote_slide(narrative: NarrativeOutput) -> Image.Image:
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Short accent bar above the quote.
    bar_y = 380
    bar_half_w = 70
    cx = SLIDE_W // 2
    draw.rectangle((cx - bar_half_w, bar_y, cx + bar_half_w, bar_y + 4), fill=ACCENT_COLOR)

    quote_font = _load_font(SERIF_PATHS, size=62)
    text = f"“{narrative.pull_quote_en}”"  # curly double quotes
    lines = _wrap_text(text, quote_font, max_width=SLIDE_W - 180)
    _draw_centered_block(draw, lines, quote_font, INK_COLOR, top=bar_y + 60, line_spacing=22)

    return img


# ── font loading ─────────────────────────────────────────────────────────────


def _load_font(paths: tuple[str, ...], *, size: int) -> ImageFont.FreeTypeFont:
    """Try each path in order; fall back to Pillow's bundled DejaVu Sans.

    The fallback is functional but visually unpolished — bundling Inter or
    similar would improve the carousel typography on Linux boxes that lack
    a system serif. Tracked as a follow-up.
    """
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size)
            except OSError:  # pragma: no cover — malformed system font
                continue
    logger.warning(
        "no usable system serif found at any of %d paths; falling back to "
        "Pillow's bundled DejaVu Sans. Carousel typography will be plainer.",
        len(paths),
    )
    fallback = ImageFont.load_default(size=size)
    # load_default with size= returns FreeTypeFont in Pillow ≥ 10.1.
    assert isinstance(fallback, ImageFont.FreeTypeFont)
    return fallback


# ── text layout ──────────────────────────────────────────────────────────────


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, *, max_width: int) -> list[str]:
    """Greedy word-wrap to lines that fit within ``max_width`` pixels."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for word in words[1:]:
        candidate = f"{cur} {word}"
        bbox = font.getbbox(candidate)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            cur = candidate
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def _text_size(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    *,
    y: int,
) -> None:
    width, _ = _text_size(draw, text, font)
    draw.text(((SLIDE_W - width) // 2, y), text, fill=color, font=font)


def _draw_centered_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    *,
    top: int,
    line_spacing: int,
) -> int:
    """Draw a vertically-stacked, horizontally-centered block of lines.

    Returns the y-coordinate just below the last line, useful for chaining
    block placement without re-measuring.
    """
    y = top
    for line in lines:
        width, height = _text_size(draw, line, font)
        draw.text(((SLIDE_W - width) // 2, y), line, fill=color, font=font)
        y += height + line_spacing
    return y - line_spacing


# ── I/O ──────────────────────────────────────────────────────────────────────


def _save_jpeg(img: Image.Image, path: Path, *, quality: int = DEFAULT_JPEG_QUALITY) -> None:
    img.save(path, format="JPEG", quality=quality, optimize=True)
