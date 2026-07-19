"""HTML renderer for the shareable memory page.

Renders ``templates/memory.html.j2`` to a single self-contained ``.html``
file at ``output_dir/{slug}.html``. Photos are embedded as
``data:image/jpeg;base64,...`` URIs so the file works offline and in any
browser — including in regions where common image CDNs are unreachable
(see ADR-001).

Public surface is :func:`render_html`. Everything else is a helper.
"""

from __future__ import annotations

import base64
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader, select_autoescape

from trailstory.gpx import elevation_profile
from trailstory.models import BUILT_STYLES, Memory, PhotoMeta, Style

logger = logging.getLogger(__name__)

TEMPLATE_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "templates"
TEMPLATE_NAME: Final[str] = "memory.html.j2"
ELEVATION_POINTS: Final[int] = 40

# Per-style WOFF2 subsets — committed under templates/fonts/<dir>/ and
# embedded as base64 data URIs so the rendered HTML works offline (ADR-001).
# Every family carries latin + cyrillic subsets; briefs that named a
# latin-only face got a Cyrillic-capable stand-in (Source Serif 4 for
# Newsreader, Oswald for Big Shoulders, JetBrains Mono for Space Mono) —
# see the LICENSE.md next to each font set.
# Maps style → {template role: (fonts subdir, filename)}.
_STYLE_FONT_FILES: Final[dict[Style, dict[str, tuple[str, str]]]] = {
    Style.letter: {
        "serif_italic_latin": ("letter", "SourceSerif4-Italic-VF.latin.woff2"),
        "serif_italic_cyrillic": ("letter", "SourceSerif4-Italic-VF.cyrillic.woff2"),
        "serif_roman_latin": ("letter", "SourceSerif4-Roman-VF.latin.woff2"),
        "serif_roman_cyrillic": ("letter", "SourceSerif4-Roman-VF.cyrillic.woff2"),
        "mono_latin": ("letter", "JetBrainsMono-VF.latin.woff2"),
        "mono_cyrillic": ("letter", "JetBrainsMono-VF.cyrillic.woff2"),
    },
    Style.zine: {
        "display_latin": ("zine", "Oswald-VF.latin.woff2"),
        "display_cyrillic": ("zine", "Oswald-VF.cyrillic.woff2"),
        # The zine body reuses the letter mono subsets (same files, same
        # base64 payload) rather than committing a duplicate copy.
        "mono_latin": ("letter", "JetBrainsMono-VF.latin.woff2"),
        "mono_cyrillic": ("letter", "JetBrainsMono-VF.cyrillic.woff2"),
    },
}


class HtmlRenderError(Exception):
    """Raised when the HTML output cannot be produced.

    This is the only exception type that should escape this module.
    Callers (CLI) catch it to surface a clean error to the user.
    """


def render_html(
    *,
    memory: Memory,
    output_dir: Path,
    slug: str,
    hike_date: date | None = None,
    location: str | None = None,
) -> Path:
    """Render a self-contained HTML memory page.

    Args:
        memory: The full hike memory. ``memory.selected_photos`` is the
            already-filtered display list (caller resolved
            ``narrative.selected_photo_indices`` into ``PhotoMeta`` objects);
            ``memory.narrative`` and ``memory.gpx_stats`` feed the hero,
            stats strip, and inline elevation SVG.
        output_dir: Directory the file is written to. Created if missing.
        slug: URL-safe identifier; the output filename is ``{slug}.html``.
        hike_date: Optional date shown in the hero meta line.
        location: Optional location label shown in the hero meta line.

    Returns:
        Absolute path to the rendered HTML file.

    Raises:
        HtmlRenderError: ``memory.selected_photos`` is empty, slug is empty,
            or a photo file cannot be read from disk.
    """
    if not memory.selected_photos:
        raise HtmlRenderError("at least one photo is required to render the memory page")
    if not slug:
        raise HtmlRenderError("slug must be a non-empty string")
    if memory.style not in BUILT_STYLES:
        raise HtmlRenderError(
            f"style {memory.style.value!r} has no renderer template yet (ADR-021); "
            f"built styles: {', '.join(sorted(s.value for s in BUILT_STYLES))}"
        )

    env = _environment()
    template = env.get_template(TEMPLATE_NAME)

    # ADR-014 / Phase 4: paragraphs is now list[Paragraph] with per-sentence
    # provenance. Provenance is not rendered (ADR-020 removed the Notes
    # audit UI); templates walk the structure for sentence text only.
    # ``paragraphs_as_localized`` stays available for templates that
    # prefer the flat shape. Pass both into the template context.
    flat_paragraphs = memory.narrative.paragraphs_as_localized()

    rendered = template.render(
        narrative=memory.narrative,
        flat_paragraphs=flat_paragraphs,
        stats=memory.gpx_stats,
        # ADR-017: optional "about this place" block. ``None`` unless the
        # --place pass ran and resolved; the template guards on it.
        place=memory.place_context,
        photos=[_photo_context(p) for p in memory.selected_photos],
        elevation=elevation_profile(memory.gpx_stats, n=ELEVATION_POINTS),
        meta={
            "slug": slug,
            "date": hike_date.isoformat() if hike_date else "",
            "location": location or "",
            "style": memory.style.value,
        },
        fonts=_style_fonts(memory.style),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{slug}.html"
    out_path.write_text(rendered, encoding="utf-8")
    logger.info("rendered memory page to %s", out_path)
    return out_path


# -- internal helpers ---------------------------------------------------------


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        # `.html.j2` ends in `.j2`; we want autoescape on for our HTML output.
        autoescape=select_autoescape(("html", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


@lru_cache(maxsize=len(_STYLE_FONT_FILES))
def _style_fonts(style: Style) -> dict[str, str]:
    """Return the style's WOFF2 fonts as base64 payloads, keyed by role.

    Read once per process per style. The returned mapping is what the
    style template under ``templates/styles/`` uses inside its
    ``@font-face`` declarations — each value is the base64 payload only
    (no ``data:font/woff2;base64,`` prefix), so the template can
    construct full ``src: url(...)`` expressions. Styles without an
    entry in ``_STYLE_FONT_FILES`` get an empty mapping.
    """
    encoded: dict[str, str] = {}
    for role, (subdir, filename) in _STYLE_FONT_FILES.get(style, {}).items():
        path = TEMPLATE_DIR / "fonts" / subdir / filename
        try:
            encoded[role] = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise HtmlRenderError(f"unable to read {style.value} font {path}: {exc}") from exc
    return encoded


def _photo_context(photo: PhotoMeta) -> dict[str, str | int]:
    try:
        raw = photo.path.read_bytes()
    except OSError as exc:
        raise HtmlRenderError(f"unable to read photo {photo.path}: {exc}") from exc
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "data_uri": f"data:image/jpeg;base64,{encoded}",
        "index": photo.index,
    }
