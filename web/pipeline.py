"""Glue between an upload and the existing trailstory pipeline.

The CLI entry point (``trailstory.cli``) and the web service share the
same pipeline shape — parse GPX, load photos, generate narrative, render
HTML — but their orchestration is different (Click vs FastAPI; tmp dir
lifecycle vs user-chosen output dir; cache vs no-cache). This module
keeps the *pipeline* logic in one place so the route handlers stay thin.

Public surface:

* :class:`Style` — three-value enum mirroring the form's radio buttons
  (editorial / log / encyclopedia). Per ADR-006 the style chooses the
  visual template only — the narrative text is identical across styles.
  Today only the editorial template is wired up; ``log`` and
  ``encyclopedia`` accept the same template until their layouts ship.
* :func:`run_pipeline` — takes a ``Workspace`` and form-derived inputs,
  returns a populated ``Memory`` and the path of the rendered HTML.
* :func:`render_carousel` — re-builds the ``Memory`` from
  ``workspace.state_path`` and renders an Instagram carousel.

Errors from the underlying pipeline are surfaced as
:class:`PipelineError`; the routes layer maps that to a 400 response.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from trailstory.gpx import GpxParseError, parse_gpx
from trailstory.llm.client import AnthropicClient
from trailstory.llm.narrative import NarrativeGenerationError, generate_narrative
from trailstory.models import HikeInput, Memory, NarrativeOutput, PhotoMeta
from trailstory.photos import PhotoLoadError, load_photos
from trailstory.renderers.html import HtmlRenderError, render_html
from trailstory.renderers.instagram import InstagramRenderError, render_instagram_carousel
from web.storage import Workspace

logger = logging.getLogger(__name__)

# Carousel JPEG quality. Mirrors the CLI default; if we ever surface a
# user-tunable knob this is the value to expose.
CAROUSEL_QUALITY: Final[int] = 90


class Style(StrEnum):
    """Visual style picked by the user on the builder form.

    The narrative is identical across styles (one prompt, one
    NarrativeOutput); only the rendering template differs. See
    `docs/adr/006-three-visual-styles-share-one-narrative.md`.
    """

    editorial = "editorial"
    log = "log"
    encyclopedia = "encyclopedia"

    @classmethod
    def default(cls) -> Style:
        return cls.editorial


class PipelineError(Exception):
    """Terminal failure during a web pipeline run.

    The route handler catches this and surfaces a 400 with the message.
    Wraps the four domain errors (`GpxParseError`, `PhotoLoadError`,
    `NarrativeGenerationError`, `HtmlRenderError`) so callers do not
    need to import them.
    """


def run_pipeline(
    workspace: Workspace,
    *,
    description: str,
    style: Style,
    client: AnthropicClient,
    photo_max_edge: int,
    photo_quality: int,
    location: str | None = None,
) -> tuple[Memory, Path]:
    """Run parse → load_photos → narrative → render_html for a workspace.

    Inputs (raw GPX + photos) must already exist under
    ``workspace.gpx_dir`` and ``workspace.photos_dir``. The resized
    photos go to ``workspace.resized_dir`` and the rendered HTML goes
    to ``workspace.output_dir``.

    The fully-populated ``Memory`` is also persisted as JSON to
    ``workspace.state_path`` so :func:`render_carousel` can rebuild it
    later — the raw uploads are about to be wiped by the route's
    BackgroundTask.

    Style is recorded on the ``Memory`` for forward compatibility with
    ADR-006 (per-style templates), but only the editorial template is
    wired up today; ``log`` and ``encyclopedia`` render the same way.
    """
    gpx_path = _single_gpx_file(workspace.gpx_dir)
    try:
        stats = parse_gpx(gpx_path)
    except GpxParseError as exc:
        raise PipelineError(str(exc)) from exc

    try:
        photos = load_photos(
            workspace.photos_dir,
            workspace.resized_dir,
            max_edge=photo_max_edge,
            quality=photo_quality,
        )
    except PhotoLoadError as exc:
        raise PipelineError(str(exc)) from exc

    hike_input = HikeInput(
        gpx_path=gpx_path,
        photos_dir=workspace.photos_dir,
        seed_text=description,
        location_name=location,
    )

    try:
        narrative = generate_narrative(
            hike_input,
            stats,
            photos,
            client=client,
            use_cache=False,
        )
    except NarrativeGenerationError as exc:
        raise PipelineError(str(exc)) from exc

    selected = [photos[i] for i in narrative.selected_photo_indices if 0 <= i < len(photos)]
    if not selected:
        raise PipelineError("LLM returned no usable photo indices.")

    hike_date = _derive_hike_date(stats, photos)

    memory = Memory(
        hike_input=hike_input,
        gpx_stats=stats,
        narrative=narrative,
        selected_photos=selected,
    )

    try:
        out_path = render_html(
            memory=memory,
            output_dir=workspace.output_dir,
            slug=workspace.slug,
            hike_date=hike_date,
            location=location,
        )
    except HtmlRenderError as exc:
        raise PipelineError(str(exc)) from exc

    _persist_state(workspace, memory, hike_date=hike_date, location=location, style=style)
    logger.info("rendered %s for workspace %s (style=%s)", out_path.name, workspace.slug, style)
    return memory, out_path


def render_carousel(workspace: Workspace) -> list[Path]:
    """Generate the Instagram carousel for an existing workspace.

    Reloads the persisted ``Memory`` from ``workspace.state_path`` and
    calls :func:`trailstory.renderers.instagram.render_instagram_carousel`.
    Returns the list of slide paths in display order.

    Raises :class:`PipelineError` if the workspace's state has been
    swept or the carousel renderer fails (typically: a resized photo
    has been deleted out from under us).
    """
    state = _load_state(workspace)
    try:
        return render_instagram_carousel(
            memory=state.memory,
            output_dir=workspace.output_dir,
            slug=workspace.slug,
            hike_date=state.hike_date,
            location=state.location,
            quality=CAROUSEL_QUALITY,
        )
    except InstagramRenderError as exc:
        raise PipelineError(str(exc)) from exc


# ── persistence helpers ──────────────────────────────────────────────────────


class _State:
    """Subset of pipeline state needed to rebuild a ``Memory`` for the carousel."""

    __slots__ = ("hike_date", "location", "memory", "style")

    def __init__(
        self,
        memory: Memory,
        hike_date: date | None,
        location: str | None,
        style: Style,
    ) -> None:
        self.memory = memory
        self.hike_date = hike_date
        self.location = location
        self.style = style


def _persist_state(
    workspace: Workspace,
    memory: Memory,
    *,
    hike_date: date | None,
    location: str | None,
    style: Style,
) -> None:
    """Write the small JSON blob the carousel route needs.

    We persist the ``Memory`` plus the display metadata (hike date,
    location, style). Photo paths inside ``memory.selected_photos``
    point at ``workspace.resized_dir`` and stay valid until the
    retention sweep deletes the workspace.
    """
    payload = {
        "memory": memory.model_dump(mode="json"),
        "hike_date": hike_date.isoformat() if hike_date else None,
        "location": location,
        "style": style.value,
    }
    workspace.state_path.write_text(json.dumps(payload), encoding="utf-8")


def _load_state(workspace: Workspace) -> _State:
    if not workspace.state_path.is_file():
        raise PipelineError("workspace state has expired or never existed")
    raw = json.loads(workspace.state_path.read_text(encoding="utf-8"))
    memory = Memory.model_validate(raw["memory"])
    hike_date_raw = raw.get("hike_date")
    hike_date = date.fromisoformat(hike_date_raw) if hike_date_raw else None
    style_raw = raw.get("style") or Style.default().value
    return _State(memory, hike_date, raw.get("location"), Style(style_raw))


# ── pipeline helpers ─────────────────────────────────────────────────────────


def _single_gpx_file(gpx_dir: Path) -> Path:
    """The route handler writes exactly one GPX into ``gpx_dir``; surface
    a clean error if that invariant breaks."""
    candidates = sorted(gpx_dir.iterdir()) if gpx_dir.is_dir() else []
    files = [p for p in candidates if p.is_file()]
    if len(files) != 1:
        raise PipelineError(f"expected exactly one GPX file in {gpx_dir}, found {len(files)}")
    return files[0]


def _derive_hike_date(stats: object, photos: list[PhotoMeta]) -> date:
    """Same heuristic as the CLI: GPX trackpoint time wins, then photo, then today."""
    waypoints = getattr(stats, "waypoints", None) or []
    for w in waypoints:
        ts: datetime | None = getattr(w, "time", None)
        if ts is not None:
            return ts.date()
    if photos:
        return photos[0].timestamp.date()
    return date.today()


# Re-export NarrativeOutput so route handlers don't need to import from
# trailstory.models directly. Keeps the layering tidy.
__all__ = [
    "CAROUSEL_QUALITY",
    "NarrativeOutput",
    "PipelineError",
    "Style",
    "render_carousel",
    "run_pipeline",
]
