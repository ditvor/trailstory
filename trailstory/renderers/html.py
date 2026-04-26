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
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader, select_autoescape

from trailstory.gpx import elevation_profile
from trailstory.models import GpxStats, NarrativeOutput, PhotoMeta

logger = logging.getLogger(__name__)

TEMPLATE_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "templates"
TEMPLATE_NAME: Final[str] = "memory.html.j2"
ELEVATION_POINTS: Final[int] = 40


class HtmlRenderError(Exception):
    """Raised when the HTML output cannot be produced.

    This is the only exception type that should escape this module.
    Callers (CLI) catch it to surface a clean error to the user.
    """


def render_html(
    *,
    narrative: NarrativeOutput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    output_dir: Path,
    slug: str,
    hike_date: date | None = None,
    location: str | None = None,
) -> Path:
    """Render a self-contained HTML memory page.

    Args:
        narrative: Validated bilingual narrative produced by the LLM pipeline.
        gpx_stats: Hike stats. Used both for the stats strip and for the
            inline elevation-profile SVG.
        photos: Photos to embed, in display order. Caller is responsible for
            having already filtered ``narrative.selected_photo_indices``
            into this list.
        output_dir: Directory the file is written to. Created if missing.
        slug: URL-safe identifier; the output filename is ``{slug}.html``.
        hike_date: Optional date shown in the hero meta line.
        location: Optional location label shown in the hero meta line.

    Returns:
        Absolute path to the rendered HTML file.

    Raises:
        HtmlRenderError: photos is empty, slug is empty, or a photo file
            cannot be read from disk.
    """
    if not photos:
        raise HtmlRenderError("at least one photo is required to render the memory page")
    if not slug:
        raise HtmlRenderError("slug must be a non-empty string")

    env = _environment()
    template = env.get_template(TEMPLATE_NAME)

    rendered = template.render(
        narrative=narrative,
        stats=gpx_stats,
        photos=[_photo_context(p) for p in photos],
        elevation=elevation_profile(gpx_stats, n=ELEVATION_POINTS),
        meta={
            "slug": slug,
            "date": hike_date.isoformat() if hike_date else "",
            "location": location or "",
        },
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
