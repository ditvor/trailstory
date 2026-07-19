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
from pydantic import SecretStr
from rich.console import Console

from trailstory.config import load_settings
from trailstory.gpx import GpxParseError, parse_gpx
from trailstory.llm.client import AnthropicClient
from trailstory.llm.narrative import (
    LedgerExtractionError,
    NarrativeGenerationError,
    extract_ledger,
    generate_narrative,
)
from trailstory.llm.place import generate_place_context, place_beats_from_ledger
from trailstory.models import (
    BUILT_STYLES,
    FactLedger,
    GpxStats,
    HikeInput,
    Memory,
    PhotoMeta,
    PlaceContext,
    Style,
)
from trailstory.photos import PhotoLoadError, describe_photos, load_photos
from trailstory.place import representative_coordinate, resolve_place_reference
from trailstory.poi import resolve_poi_matches
from trailstory.renderers.html import HtmlRenderError, render_html
from trailstory.renderers.instagram import InstagramRenderError, render_instagram_carousel

console = Console()
logger = logging.getLogger(__name__)


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
@click.option(
    "--place",
    "place",
    is_flag=True,
    default=False,
    help="Add an 'about this place' block (ADR-017): reverse-geocode the track, "
    "fetch a grounded place description, and weave in the hiker's own beats. "
    "Off by default — sends the track's coordinates to an external service.",
)
@click.option(
    "--poi",
    "poi",
    is_flag=True,
    default=False,
    help="Resolve the hiker's landmarks to real OSM names (ADR-019), e.g. "
    "'the wax-figure church' → 'Mühlfeldkirche'. Implies --place; queries "
    "OpenStreetMap. Conservative: names a landmark only when the match is "
    "unambiguous.",
)
@click.option(
    "--style",
    "style",
    type=click.Choice(sorted(s.value for s in BUILT_STYLES), case_sensitive=False),
    default=Style.letter.value,
    show_default=True,
    help="Visual treatment of the rendered memory page (see ADR-006, ADR-021). "
    "All styles share one narrative; only layout and CSS differ. Planned "
    "styles (sunday, album) appear here once built.",
)
def generate(
    photos_path: Path,
    gpx_path: Path,
    seed: str,
    out_dir_arg: Path | None,
    location: str | None,
    instagram: bool,
    no_cache: bool,
    place: bool,
    poi: bool,
    style: str,
) -> None:
    """Generate a shareable HTML memory page from a hike."""
    settings = load_settings()
    # ADR-019: --poi implies the place block (it has nowhere else to land).
    place = place or poi
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
            # Phase 2 (ADR-009): the ledger extractor runs on a separate,
            # cheaper model so the writer's Opus budget is spent only on
            # the prose pass. Built here rather than reused so the two
            # passes are independently configurable via env vars.
            ledger_client = AnthropicClient(
                settings.anthropic_api_key,
                model=settings.ledger_model,
                max_tokens=settings.narrative_max_tokens,
                max_retries=settings.narrative_max_retries,
            )
            # Phase 3 (ADR-010): per-photo vision describer. Same model
            # family as the ledger extractor by default (both Haiku-class);
            # split client so vision can be overridden / disabled via
            # Settings independently of the ledger pass.
            vision_client = AnthropicClient(
                settings.anthropic_api_key,
                model=settings.vision_model,
                max_tokens=settings.narrative_max_tokens,
                max_retries=settings.narrative_max_retries,
            )

            if settings.use_photo_grounding:
                with console.status("Describing photos…", spinner="dots"):
                    photos = describe_photos(
                        photos,
                        client=vision_client,
                        enabled=True,
                        concurrency=settings.vision_concurrency,
                        use_cache=not no_cache,
                    )

            # ADR-017: when --place is set, extract the ledger once up front
            # so it feeds both the writer (passed in below) and the place
            # block's "hiker's own beats". A ledger failure here disables the
            # place block but never aborts the run — generate_narrative
            # re-extracts internally and surfaces its own errors.
            ledger: FactLedger | None = None
            if place:
                try:
                    ledger = extract_ledger(
                        hike_input,
                        stats,
                        photos,
                        client=ledger_client,
                        location=location or "the trail",
                    )
                except LedgerExtractionError as exc:
                    logger.warning(
                        "place: ledger extraction failed (%s); skipping place block", exc
                    )

            with console.status("Generating narrative…", spinner="dots"):
                narrative = generate_narrative(
                    hike_input,
                    stats,
                    photos,
                    client=client,
                    ledger_client=ledger_client,
                    use_cache=not no_cache,
                    max_inferred_ratio=settings.max_inferred_ratio,
                    ledger=ledger,
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

            place_context: PlaceContext | None = None
            if place and ledger is not None:
                with console.status("Resolving place…", spinner="dots"):
                    place_context = _resolve_place(
                        stats,
                        ledger,
                        location_name=location,
                        resolve_poi=poi,
                        api_key=settings.anthropic_api_key,
                        model=settings.place_model,
                        max_tokens=settings.narrative_max_tokens,
                        max_retries=settings.narrative_max_retries,
                    )
                if place_context is not None:
                    extra = (
                        f" · {len(place_context.named_landmarks)} landmark(s) named"
                        if place_context.named_landmarks
                        else ""
                    )
                    console.print(f"[green]✓[/] Place context added — {place_context.town}{extra}")
                else:
                    console.print("[yellow]·[/] No place context (geocode or stitch unavailable)")

            memory = Memory(
                hike_input=hike_input,
                gpx_stats=stats,
                narrative=narrative,
                selected_photos=selected,
                style=Style(style),
                place_context=place_context,
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


def _resolve_place(
    stats: GpxStats,
    ledger: FactLedger,
    *,
    location_name: str | None,
    resolve_poi: bool,
    api_key: SecretStr,
    model: str,
    max_tokens: int,
    max_retries: int,
) -> PlaceContext | None:
    """Build the ADR-017 place block, or return ``None`` if unavailable.

    Reverse-geocodes the track midpoint (preferring the hiker's own
    ``location_name`` for the town), fetches a grounded reference extract,
    optionally resolves the hiker's landmark beats to real OSM names
    (``resolve_poi``, ADR-019), and stitches it all with the hiker's own
    ledger beats. Every failure mode soft-fails to ``None`` (block omitted)
    or an empty match list (names skipped) — never a broken render.
    """
    coord = representative_coordinate(stats.waypoints)
    if coord is None:
        return None
    reference = resolve_place_reference(*coord, location_name=location_name)
    if reference is None:
        return None
    beats = place_beats_from_ledger(ledger)
    poi_matches = resolve_poi_matches(stats.waypoints, beats) if resolve_poi else []
    place_client = AnthropicClient(
        api_key,
        model=model,
        max_tokens=max_tokens,
        max_retries=max_retries,
    )
    return generate_place_context(
        reference,
        beats,
        client=place_client,
        poi_matches=poi_matches,
    )


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
