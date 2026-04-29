"""Command-line entry point for Trailstory.

Per CLAUDE.md, this module stays thin: it parses arguments, orchestrates
the pipeline (``parse_gpx`` → ``load_photos`` → ``generate_narrative`` →
``render_html``), and surfaces progress to the user via ``rich``. The
heavy lifting lives in the dedicated modules.

Domain-level errors are caught and rendered as a one-line red message
with a non-zero exit code. Unexpected exceptions are deliberately not
caught — those are bugs and should bubble up with a full traceback.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NoReturn

import click
from rich.console import Console

from trailstory.config import load_settings
from trailstory.gpx import GpxParseError, parse_gpx
from trailstory.llm.client import AnthropicClient
from trailstory.llm.narrative import NarrativeGenerationError, generate_narrative
from trailstory.models import GpxStats, HikeInput, Memory, PhotoMeta
from trailstory.photos import PhotoLoadError, load_photos
from trailstory.renderers.html import HtmlRenderError, render_html
from trailstory.renderers.instagram import InstagramRenderError, render_instagram_carousel

console = Console()


@click.group()
def cli() -> None:
    """Trailstory — turn a hike into a memory worth sharing."""


@cli.command("generate")
@click.option(
    "--photos",
    "photos_path",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    help="Directory of hike photos (.jpg / .heic).",
)
@click.option(
    "--gpx",
    "gpx_path",
    required=True,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
    help="GPX track file.",
)
@click.option(
    "--seed",
    required=True,
    help="2-3 sentence emotional seed describing the hike.",
)
@click.option(
    "--out",
    "out_dir_arg",
    default=None,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Output directory. Defaults to OUTPUT_DIR from settings (./output).",
)
@click.option(
    "--location",
    default=None,
    help="Optional human-readable location, e.g. 'Bavarian Alps'.",
)
@click.option(
    "--instagram",
    is_flag=True,
    default=False,
    help="Also generate a 1080x1350 Instagram carousel under {out}/{slug}/carousel/.",
)
@click.option(
    "--no-cache",
    "no_cache",
    is_flag=True,
    default=False,
    help="Skip the on-disk narrative cache for this run (forces a fresh LLM call).",
)
def generate(
    photos_path: Path,
    gpx_path: Path,
    seed: str,
    out_dir_arg: Path | None,
    location: str | None,
    instagram: bool,
    no_cache: bool,
) -> None:
    """Generate a shareable HTML memory page from a hike."""
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())
    out_dir = out_dir_arg if out_dir_arg is not None else settings.output_dir

    try:
        with console.status("Parsing GPX…", spinner="dots"):
            stats = parse_gpx(gpx_path)
        console.print(
            f"[green]✓[/] GPX parsed — "
            f"{stats.distance_km} km · {stats.elevation_gain_m:.0f} m gain · "
            f"{stats.duration_min} min"
        )

        # Resized photos are intermediate; the renderer base64-embeds them
        # so they can vanish once the HTML is written.
        with TemporaryDirectory(prefix="trailstory-resized-") as tmp:
            with console.status("Loading photos…", spinner="dots"):
                photos = load_photos(
                    photos_path,
                    Path(tmp),
                    max_edge=settings.photo_max_edge,
                    quality=settings.photo_quality,
                )
            console.print(f"[green]✓[/] {len(photos)} photos found")

            hike_input = HikeInput(
                gpx_path=gpx_path,
                photos_dir=photos_path,
                seed_text=seed,
                location_name=location,
            )
            client = AnthropicClient(
                settings.anthropic_api_key,
                model=settings.model,
                max_tokens=settings.narrative_max_tokens,
                max_retries=settings.narrative_max_retries,
            )
            with console.status("Generating narrative…", spinner="dots"):
                narrative = generate_narrative(
                    hike_input,
                    stats,
                    photos,
                    client=client,
                    use_cache=not no_cache,
                )
            console.print(
                f"[green]✓[/] Narrative generated "
                f"({len(narrative.selected_photo_indices)} photos selected)"
            )

            selected = [photos[i] for i in narrative.selected_photo_indices if 0 <= i < len(photos)]
            if not selected:
                _abort("LLM returned no usable photo indices.")

            hike_date = _derive_hike_date(stats, photos)
            slug = _derive_slug(hike_date, location)

            memory = Memory(
                hike_input=hike_input,
                gpx_stats=stats,
                narrative=narrative,
                selected_photos=selected,
            )

            with console.status("Rendering HTML…", spinner="dots"):
                out_path = render_html(
                    memory=memory,
                    output_dir=out_dir,
                    slug=slug,
                    hike_date=hike_date,
                    location=location,
                )
            console.print(f"[green]✓[/] Page rendered → {out_path}")

            if instagram:
                # Carousel reads the resized JPEGs, so it must run inside the
                # TemporaryDirectory context.
                with console.status("Rendering Instagram carousel…", spinner="dots"):
                    carousel_paths = render_instagram_carousel(
                        memory=memory,
                        output_dir=out_dir,
                        slug=slug,
                        hike_date=hike_date,
                        location=location,
                        quality=settings.instagram_quality,
                    )
                console.print(
                    f"[green]✓[/] Carousel rendered ({len(carousel_paths)} slides) "
                    f"→ {carousel_paths[0].parent}"
                )
        console.print(f"\n  open in any browser: file://{out_path.resolve()}")
    except (
        GpxParseError,
        PhotoLoadError,
        NarrativeGenerationError,
        HtmlRenderError,
        InstagramRenderError,
    ) as exc:
        _abort(str(exc))


# -- internal helpers ---------------------------------------------------------


def _abort(msg: str) -> NoReturn:
    console.print(f"[red]error:[/] {msg}")
    sys.exit(1)


def _derive_hike_date(stats: GpxStats, photos: list[PhotoMeta]) -> date:
    """Pick the most authoritative date available.

    GPX trackpoint times beat photo timestamps (the GPX is authored by the
    device that actually moved); photo timestamps beat ``date.today()``.
    """
    for w in stats.waypoints:
        if w.time is not None:
            return w.time.date()
    if photos:
        return photos[0].timestamp.date()
    return date.today()


def _derive_slug(hike_date: date, location: str | None) -> str:
    """Build a stable filename slug like ``2025-08-15-bavarian-alps``."""
    location_slug = _slugify(location) if location else ""
    return f"{hike_date.isoformat()}-{location_slug or 'hike'}"


def _slugify(text: str) -> str:
    """Lowercase ASCII-only slug. Non-ASCII collapses to empty."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
