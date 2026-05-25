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
* :func:`prepare_pipeline` — runs the deterministic prep phase (parse +
  load_photos) and persists the inputs as ``pending.json`` so the SSE
  endpoint can resume with a streaming LLM call.
* :func:`stream_pipeline` — yields :class:`PipelineStreamEvent`
  instances as the LLM writes the narrative; on success renders the
  HTML and persists final ``state.json``.
* :func:`render_carousel` — re-builds the ``Memory`` from
  ``workspace.state_path`` and renders an Instagram carousel.

Errors from the underlying pipeline are surfaced as
:class:`PipelineError`; the routes layer maps that to a 400 response.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from trailstory.gpx import GpxParseError, parse_gpx
from trailstory.llm.client import AnthropicClient
from trailstory.llm.narrative import (
    NarrativeGenerationError,
    NarrativeStreamChunk,
    NarrativeStreamComplete,
    NarrativeStreamRetry,
    generate_narrative_stream,
)
from trailstory.models import GpxStats, HikeInput, Memory, NarrativeOutput, PhotoMeta
from trailstory.models import Style as _ModelStyle
from trailstory.photos import PhotoLoadError, describe_photos, load_photos
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


@dataclass(frozen=True)
class PipelineStreamChunk:
    """A text delta from the streaming narrative call.

    The SSE endpoint forwards each chunk to the browser so the user sees
    the model writing in real time.
    """

    text: str


@dataclass(frozen=True)
class PipelineStreamRetry:
    """First attempt failed validation; retrying once.

    The SSE endpoint flips the page UI to a "regenerating" state when
    this event lands.
    """

    reason: str


@dataclass(frozen=True)
class PipelineStreamRendered:
    """Final event: HTML written, ``state.json`` persisted, slug ready to redirect to."""

    slug: str
    output_path: Path


PipelineStreamEvent = PipelineStreamChunk | PipelineStreamRetry | PipelineStreamRendered


def prepare_pipeline(
    workspace: Workspace,
    *,
    description: str,
    style: Style,
    photo_max_edge: int,
    photo_quality: int,
    location: str | None = None,
) -> None:
    """Parse + load photos + persist pending state for the streaming step.

    Reads exactly one GPX from ``workspace.gpx_dir`` and every photo
    from ``workspace.photos_dir``, writes resized JPEGs to
    ``workspace.resized_dir`` (which survives the BackgroundTask
    cleanup of the raw uploads), and writes ``pending.json`` so the
    SSE endpoint can resume with a streaming LLM call without
    re-reading the inputs.

    Raises :class:`PipelineError` for parse/load failures.
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
    hike_date = _derive_hike_date(stats, photos)

    _persist_pending_state(
        workspace,
        hike_input=hike_input,
        gpx_stats=stats,
        photos=photos,
        hike_date=hike_date,
        location=location,
        style=style,
    )
    logger.info(
        "prepared workspace %s for streaming (style=%s, photos=%d)",
        workspace.slug,
        style.value,
        len(photos),
    )


def stream_pipeline(
    workspace: Workspace,
    *,
    client: AnthropicClient,
    ledger_client: AnthropicClient,
    vision_client: AnthropicClient,
    use_photo_grounding: bool = True,
) -> Iterator[PipelineStreamEvent]:
    """Resume a prepared pipeline run and stream the narrative.

    Reads the pending state written by :func:`prepare_pipeline`, runs
    the Phase 3 (ADR-010) vision describer over the photos (one Haiku
    vision call per photo, synchronous), then calls the two-pass
    narrative pipeline (ADR-009 — Haiku ledger extractor + Opus
    writer). Yields :class:`PipelineStreamEvent` instances as writer
    chunks arrive; on success renders the HTML and persists the final
    ``state.json``. The terminal event is always
    :class:`PipelineStreamRendered`.

    The vision pass + extractor pass land before the SSE writer chunks
    start streaming. Latency-wise that's the first ~3-5 seconds of the
    "Generating…" spinner; the user sees the writer's prose appear
    after that. Set ``use_photo_grounding=False`` to skip vision
    (cheaper, faster start, writer loses photo-grounded specifics).

    Raises :class:`PipelineError` if the workspace has no pending state,
    the LLM call fails, validation fails, or HTML rendering fails.
    """
    pending = _load_pending_state(workspace)
    # Phase 3: describe photos before the writer runs. Failures inside
    # describe_photos are non-fatal (per-photo skip + warn); the
    # narrative pipeline handles a partial description list.
    photos_with_descriptions = describe_photos(
        pending.photos, client=vision_client, enabled=use_photo_grounding
    )
    try:
        narrative: NarrativeOutput | None = None
        for event in generate_narrative_stream(
            pending.hike_input,
            pending.gpx_stats,
            photos_with_descriptions,
            client=client,
            ledger_client=ledger_client,
        ):
            if isinstance(event, NarrativeStreamChunk):
                yield PipelineStreamChunk(text=event.text)
            elif isinstance(event, NarrativeStreamRetry):
                yield PipelineStreamRetry(reason=event.reason)
            elif isinstance(event, NarrativeStreamComplete):
                narrative = event.narrative
    except NarrativeGenerationError as exc:
        raise PipelineError(str(exc)) from exc

    if narrative is None:
        # Defensive: generate_narrative_stream always ends with a
        # NarrativeStreamComplete on success, so reaching this branch
        # means the generator returned without yielding the terminal
        # event — treat as a hard failure.
        raise PipelineError("narrative stream ended without a final event")

    selected = [
        pending.photos[i] for i in narrative.selected_photo_indices if 0 <= i < len(pending.photos)
    ]
    if not selected:
        raise PipelineError("LLM returned no usable photo indices.")

    memory = Memory(
        hike_input=pending.hike_input,
        gpx_stats=pending.gpx_stats,
        narrative=narrative,
        selected_photos=selected,
        # Convert across the (deliberately separate, see ADR-006) Style
        # enums in this module and trailstory.models — they share string
        # values so the round-trip is lossless.
        style=_ModelStyle(pending.style.value),
    )

    try:
        out_path = render_html(
            memory=memory,
            output_dir=workspace.output_dir,
            slug=workspace.slug,
            hike_date=pending.hike_date,
            location=pending.location,
        )
    except HtmlRenderError as exc:
        raise PipelineError(str(exc)) from exc

    _persist_state(
        workspace,
        memory,
        hike_date=pending.hike_date,
        location=pending.location,
        style=pending.style,
    )
    workspace.pending_state_path.unlink(missing_ok=True)
    logger.info(
        "streamed pipeline rendered %s for workspace %s (style=%s)",
        out_path.name,
        workspace.slug,
        pending.style.value,
    )
    yield PipelineStreamRendered(slug=workspace.slug, output_path=out_path)


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


class _PendingState:
    """Pre-LLM inputs needed to resume into the streaming narrative call."""

    __slots__ = (
        "gpx_stats",
        "hike_date",
        "hike_input",
        "location",
        "photos",
        "style",
    )

    def __init__(
        self,
        hike_input: HikeInput,
        gpx_stats: GpxStats,
        photos: list[PhotoMeta],
        hike_date: date | None,
        location: str | None,
        style: Style,
    ) -> None:
        self.hike_input = hike_input
        self.gpx_stats = gpx_stats
        self.photos = photos
        self.hike_date = hike_date
        self.location = location
        self.style = style


def _persist_pending_state(
    workspace: Workspace,
    *,
    hike_input: HikeInput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    hike_date: date | None,
    location: str | None,
    style: Style,
) -> None:
    """Write the pre-LLM state ``stream_pipeline`` will resume from.

    The photos list (paths into ``workspace.resized_dir``) is what
    survives the BackgroundTask cleanup of the raw uploads; we capture
    the resolved metadata here so the SSE endpoint can pick up without
    reaching back into the input dir.
    """
    payload = {
        "hike_input": hike_input.model_dump(mode="json"),
        "gpx_stats": gpx_stats.model_dump(mode="json"),
        "photos": [p.model_dump(mode="json") for p in photos],
        "hike_date": hike_date.isoformat() if hike_date else None,
        "location": location,
        "style": style.value,
    }
    workspace.pending_state_path.write_text(json.dumps(payload), encoding="utf-8")


def _load_pending_state(workspace: Workspace) -> _PendingState:
    if not workspace.pending_state_path.is_file():
        raise PipelineError("workspace has no pending state to stream from")
    raw = json.loads(workspace.pending_state_path.read_text(encoding="utf-8"))
    hike_input = HikeInput.model_validate(raw["hike_input"])
    gpx_stats = GpxStats.model_validate(raw["gpx_stats"])
    photos = [PhotoMeta.model_validate(p) for p in raw["photos"]]
    hike_date_raw = raw.get("hike_date")
    hike_date = date.fromisoformat(hike_date_raw) if hike_date_raw else None
    style_raw = raw.get("style") or Style.default().value
    return _PendingState(
        hike_input,
        gpx_stats,
        photos,
        hike_date,
        raw.get("location"),
        Style(style_raw),
    )


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


def _derive_hike_date(stats: GpxStats, photos: list[PhotoMeta]) -> date:
    """Same heuristic as the CLI: GPX trackpoint time wins, then photo, then today."""
    for w in stats.waypoints:
        ts: datetime | None = w.time
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
    "PipelineStreamChunk",
    "PipelineStreamEvent",
    "PipelineStreamRendered",
    "PipelineStreamRetry",
    "Style",
    "prepare_pipeline",
    "render_carousel",
    "stream_pipeline",
]
