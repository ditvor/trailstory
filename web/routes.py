"""HTTP route handlers for the web builder.

Nine endpoints, all stateless from the user's point of view:

* ``GET /``                          — landing page + builder form.
* ``POST /generate``                 — multipart upload; runs the prep
                                        phase (parse + photo load) and
                                        returns the generating page.
* ``GET /generate/{slug}/stream``    — Server-Sent Events stream that
                                        runs the LLM call and renders
                                        the HTML; emits chunk / retry /
                                        done / error events.
* ``GET /memory/{slug}``             — serves the rendered HTML.
* ``POST /memory/{slug}/carousel``   — generates the IG carousel on demand.
* ``GET /privacy``                   — plain-language privacy page.
* ``GET /healthz``                   — uptime probe.
* ``GET /version``                   — build identity (git SHA from the
                                        deploy image).
* ``GET /memory/{slug}/carousel/{filename}`` — serves a single slide.

Heavy lifting (parse / load / narrative / render) lives in
``web.pipeline``; this module is just request validation, file I/O,
response shaping, and the BackgroundTask that wipes raw uploads after a
successful generate. That keeps the file readable when scanning for
attack surface.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from trailstory.config import Settings
from trailstory.gpx import GpxParseError, extract_track_name, parse_gpx
from trailstory.llm.client import AnthropicClient
from trailstory.photos import read_exif_date
from web.copy import STYLE_CARDS, resolve_lang
from web.pipeline import (
    PipelineError,
    PipelineStreamChunk,
    PipelineStreamRendered,
    PipelineStreamRetry,
    Style,
    prepare_pipeline,
    render_carousel,
    stream_pipeline,
)
from web.ratelimit import enforce_generate_limit
from web.storage import Storage, Workspace

logger = logging.getLogger(__name__)

# Upload limits. Enforced in the route so we surface a clean 413 instead
# of a generic Starlette parser error. Numbers picked by what a normal
# hike upload looks like (a 50 MB GPX would be a satellite track of an
# expedition; 30 MB per photo accommodates RAW-converted JPEGs).
MAX_GPX_BYTES: Final[int] = 50 * 1024 * 1024
MAX_PHOTO_BYTES: Final[int] = 30 * 1024 * 1024
MAX_PHOTOS_PER_HIKE: Final[int] = 60
ALLOWED_PHOTO_SUFFIXES: Final[frozenset[str]] = frozenset({".jpg", ".jpeg", ".heic", ".heif"})

# Public source-of-truth for the privacy page — the user can verify our
# stated retention behaviour against the code on disk. Hardcoded rather
# than pulled from settings because it does not vary by deployment.
REPO_URL: Final[str] = "https://github.com/ditvor/trailstory"

# Counter is intentionally process-local: a single number, no per-IP /
# per-user breakdown, no persistence. Reflects the "no analytics on
# uploaded content" privacy stance — we want to know the service is
# being used, nothing more.
_request_counter_lock = threading.Lock()
_request_counter = 0


router = APIRouter()


# ── pages ────────────────────────────────────────────────────────────────────


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> Response:
    """Builder form. Mobile-first; everything fits in one column.

    The page renders all UI copy in EN / RU / DE simultaneously; the
    active language is driven client-side by ``builderShell()`` in
    ``builder_base.html.j2``. ``?lang=`` is honoured for first paint
    so a shared link can land in the right language.
    """
    active_lang = resolve_lang(request.query_params.get("lang"))
    return _templates(request).TemplateResponse(
        request,
        "landing.html.j2",
        {
            "styles": STYLE_CARDS,
            "default_style": Style.default().value,
            "active_lang": active_lang,
        },
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> Response:
    """Plain-language description of what we do with uploaded data."""
    return _templates(request).TemplateResponse(
        request,
        "privacy.html.j2",
        {
            "retention_minutes": _storage(request).retention_seconds // 60,
            "repo_url": REPO_URL,
        },
    )


# ── live preview (no persistence) ────────────────────────────────────────────


@router.post("/preview/gpx")
async def preview_gpx(gpx: UploadFile | None = None) -> Response:
    """Parse a GPX file in-memory and return its stats as JSON.

    Used by the builder's landing page to render the "track loaded"
    card immediately after the user drops a GPX file — filename,
    point count, distance / ascent / time / detected-location, and
    a path string for the mini-route SVG.

    Nothing is persisted: the bytes are written to a temp file just
    long enough for :func:`parse_gpx` to read them, then unlinked.
    No workspace is created. The same GPX still has to be uploaded
    again with the final form submit; that's intentional — the
    preview endpoint is read-only and stateless.
    """
    if gpx is None or not gpx.filename:
        raise HTTPException(status_code=400, detail="GPX file is required")
    body = await gpx.read()
    if not body:
        raise HTTPException(status_code=400, detail="Empty GPX file")
    if len(body) > MAX_GPX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"GPX exceeds {MAX_GPX_BYTES // (1024 * 1024)} MB",
        )

    # parse_gpx wants a Path, so round-trip the bytes through a tmp
    # file and delete it on the way out. The tmp lives in the system
    # tmp dir (NamedTemporaryFile) so it's swept on reboot even if
    # the unlink races.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as fh:
        tmp_path = Path(fh.name)
        fh.write(body)
    try:
        try:
            stats = parse_gpx(tmp_path)
        except GpxParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        location_name = extract_track_name(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    hike_date = next((w.time.date() for w in stats.waypoints if w.time), None)
    track_d, endpoints = _track_svg_from_waypoints(stats.waypoints)

    return JSONResponse(
        {
            "filename": gpx.filename,
            "n_points": len(stats.waypoints),
            "distance_km": stats.distance_km,
            "elevation_gain_m": stats.elevation_gain_m,
            "duration_min": stats.duration_min,
            "summit_m": stats.summit_elev_m,
            "location_name": location_name,
            "hike_date": hike_date.isoformat() if hike_date else None,
            "track_d": track_d,
            "endpoints": endpoints,
        }
    )


@router.post("/preview/photo")
async def preview_photo(photo: UploadFile | None = None) -> Response:
    """Read EXIF DateTimeOriginal from a single photo without persisting.

    Used by the builder's landing page to populate the AUTO-EXTRACTED
    date chip with "from photo EXIF" provenance before any workspace
    is created. The photo bytes are read into memory just long enough
    to pull the EXIF date tag; nothing is written to disk and the
    GPS sub-IFD is intentionally never touched (the privacy contract
    promises we don't surface photo GPS — see ADR-001 + the privacy
    page).
    """
    if photo is None or not photo.filename:
        raise HTTPException(status_code=400, detail="Photo is required")
    body = await photo.read()
    if not body:
        raise HTTPException(status_code=400, detail="Empty photo")
    if len(body) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Photo exceeds {MAX_PHOTO_BYTES // (1024 * 1024)} MB",
        )
    parsed = read_exif_date(body)
    return JSONResponse({"hike_date": parsed.date().isoformat() if parsed else None})


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Uptime probe. Static — does not touch storage or the LLM."""
    return {"status": "ok"}


@router.get("/version")
async def version() -> dict[str, str]:
    """Build identity for the running image.

    ``git_sha`` is injected at image build time via the ``GIT_SHA``
    Docker build arg (see Dockerfile + ``make deploy``); local runs
    fall through to ``"unknown"``. ``version`` mirrors the value
    declared in ``pyproject.toml`` so a deployed bug can be tied back
    to a specific commit + release without log archaeology.
    """
    return {
        "version": "0.1.0",
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
    }


# ── pipeline ─────────────────────────────────────────────────────────────────


@router.post(
    "/generate",
    response_class=HTMLResponse,
    dependencies=[Depends(enforce_generate_limit)],
)
async def generate(
    request: Request,
    background_tasks: BackgroundTasks,
    description: Annotated[str, Form(min_length=1, max_length=1000)],
    style: Annotated[str, Form()] = Style.default().value,
    location: Annotated[str | None, Form()] = None,
    gpx: UploadFile | None = None,
    photos: list[UploadFile] | None = None,
) -> Response:
    """Validate the upload, run the prep phase, return the generating page.

    The generating page connects to ``GET /generate/{slug}/stream`` via
    Server-Sent Events to run the LLM call. We persist the parsed inputs
    (``pending.json``) before responding so the SSE endpoint can pick up
    even after the BackgroundTask has wiped the raw uploads.

    The route is rate-limited per client IP via
    :func:`web.ratelimit.enforce_generate_limit`; an over-quota client
    gets a 429 with a ``Retry-After`` header before the multipart body
    is parsed.

    Raises a 4xx if the inputs are missing, oversized, or unsupported;
    pipeline parse / photo-load errors surface as 400 here rather than
    in the SSE stream so the user gets immediate feedback.
    """
    if gpx is None or not gpx.filename:
        raise HTTPException(status_code=400, detail="GPX file is required")
    if not photos or all(not p.filename for p in photos):
        raise HTTPException(status_code=400, detail="At least one photo is required")

    # ``Style(style)`` rejects anything outside the enum, which already
    # covers the SOON placeholders (``zine``/``sunday``/``postcard``/
    # ``album``) — they aren't enum members. The picker's
    # ``coming_soon`` flag is a UX-layer concern (disabled radio,
    # ``accepted_style_values()`` for tests) and doesn't need a second
    # server-side gate. The ``log`` and ``encyclopedia`` renderers
    # remain accepted at the backend even though the picker hides them,
    # so direct POSTs (the carousel + IG button tests rely on this)
    # keep working.
    try:
        chosen_style = Style(style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown style: {style!r}") from exc

    storage = _storage(request)
    workspace = storage.create_workspace()

    try:
        await _save_gpx(gpx, workspace)
        await _save_photos(photos, workspace)
        settings = _settings(request)
        prepare_pipeline(
            workspace,
            description=description,
            style=chosen_style,
            photo_max_edge=settings.photo_max_edge,
            photo_quality=settings.photo_quality,
            location=(location or None),
        )
    except PipelineError as exc:
        storage.delete_workspace(workspace)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        storage.delete_workspace(workspace)
        raise
    except Exception:
        storage.delete_workspace(workspace)
        raise
    finally:
        # Wipe raw uploads as soon as the response is sent. The pending
        # state captured in ``prepare_pipeline`` already references the
        # resized JPEGs, so the SSE call that follows does not need the
        # originals.
        background_tasks.add_task(storage.cleanup_inputs, workspace)

    _bump_counter()
    logger.info(
        "prepared memory %s for streaming (style=%s)",
        workspace.slug,
        chosen_style.value,
    )
    return _templates(request).TemplateResponse(
        request,
        "generating.html.j2",
        {
            "slug": workspace.slug,
            "style": chosen_style.value,
            "retention_minutes": storage.retention_seconds // 60,
        },
    )


@router.get("/generate/{slug}/stream")
async def generate_stream(request: Request, slug: str) -> Response:
    """Server-Sent Events stream that drives narrative generation.

    Reads the pending state written by ``POST /generate``, calls the
    streaming LLM, and emits four kinds of events:

    * ``chunk`` — JSON ``{"text": "..."}`` for each text delta.
    * ``status`` — JSON ``{"phase": "writing"|"regenerating"|"rendering"}``.
    * ``done``  — JSON ``{"redirect": "/memory/<slug>"}`` once the HTML
                  is rendered.
    * ``error`` — JSON ``{"error": "..."}`` if anything fails.

    The SSE format is plain text/event-stream — no framework dependency
    on the front end beyond the standard ``EventSource`` API (htmx's
    ``sse-swap`` extension also consumes this shape unchanged).
    """
    storage = _storage(request)
    workspace = storage.get_workspace(slug)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Memory not found or expired")
    if not workspace.pending_state_path.is_file():
        raise HTTPException(status_code=404, detail="Memory has already been generated or expired")

    client = _client_factory(request)()
    ledger_client = _ledger_client_factory(request)()
    vision_client = _vision_client_factory(request)()
    settings: Settings = request.app.state.settings

    def event_stream() -> Iterator[bytes]:
        yield _sse_event("status", {"phase": "writing"})
        try:
            for event in stream_pipeline(
                workspace,
                client=client,
                ledger_client=ledger_client,
                vision_client=vision_client,
                use_photo_grounding=settings.use_photo_grounding,
            ):
                if isinstance(event, PipelineStreamChunk):
                    yield _sse_event("chunk", {"text": event.text})
                elif isinstance(event, PipelineStreamRetry):
                    yield _sse_event("status", {"phase": "regenerating", "reason": event.reason})
                elif isinstance(event, PipelineStreamRendered):
                    yield _sse_event("status", {"phase": "rendering"})
                    yield _sse_event("done", {"redirect": f"/memory/{event.slug}"})
        except PipelineError as exc:
            logger.warning("stream pipeline failed for %s: %s", slug, exc)
            yield _sse_event("error", {"error": str(exc)})
        except Exception:
            # Anything that isn't a PipelineError is unexpected — log
            # the trace and tell the client we failed without leaking
            # internals.
            logger.exception("unexpected stream pipeline error for %s", slug)
            yield _sse_event("error", {"error": "internal error during generation"})
            raise

    headers = {
        # Disable any reverse-proxy buffering — SSE only works if the
        # bytes reach the client as they are written.
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.get("/memory/{slug}", response_class=HTMLResponse)
async def memory_page(request: Request, slug: str) -> Response:
    """Serve the rendered HTML — either still under retention, or 404."""
    storage = _storage(request)
    html = storage.output_html_path(slug)
    if html is None:
        raise HTTPException(status_code=404, detail="Memory not found or expired")
    # Inline so browsers and messengers preview it; the file is fully
    # self-contained (base64 photos) so direct service is fine.
    return FileResponse(html, media_type="text/html")


@router.post("/memory/{slug}/carousel")
async def memory_carousel(request: Request, slug: str) -> Response:
    """Generate the IG carousel for an existing memory.

    Returns a small JSON manifest pointing at the generated slides,
    which the front-end fetches as ``/memory/{slug}/carousel/{index}.jpg``.
    """
    storage = _storage(request)
    workspace = storage.get_workspace(slug)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Memory not found or expired")

    try:
        slides = render_carousel(workspace)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse({"slides": [f"/memory/{slug}/carousel/{p.name}" for p in slides]})


@router.get("/memory/{slug}/carousel/{filename}")
async def memory_carousel_slide(request: Request, slug: str, filename: str) -> Response:
    """Serve a single carousel slide.

    Filenames come from :func:`web.pipeline.render_carousel` so the
    only legal shape is ``NN_<role>.jpg`` — but we still validate the
    path stays inside the carousel dir so a malicious caller can't
    escape via ``../``.

    Sets ``Content-Disposition: attachment; filename="<slug>-<n>.jpg"``
    so desktop clicks on the fallback download links save with a
    meaningful filename. iOS Safari's ``navigator.share({files: [...]})``
    path ignores Content-Disposition — that flow goes through fetched
    blobs and an explicit ``File`` constructor — so the header here is
    purely for the desktop fallback.
    """
    storage = _storage(request)
    workspace = storage.get_workspace(slug)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Memory not found or expired")
    carousel_root = workspace.carousel_dir.resolve()
    target = (workspace.carousel_dir / filename).resolve()
    if carousel_root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid slide path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Slide not found")
    # Use the slide's own filename — `01_photo.jpg` etc. — namespaced by
    # slug so multiple downloads land with distinct names in the user's
    # Downloads folder.
    download_name = f"{slug}-{target.name}"
    return FileResponse(
        target,
        media_type="image/jpeg",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


# ── upload validation + persistence ──────────────────────────────────────────


async def _save_gpx(upload: UploadFile, workspace: Workspace) -> None:
    if upload.filename and not upload.filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="GPX file must have a .gpx extension")
    target = workspace.gpx_dir / "track.gpx"
    await _stream_to_disk(upload, target, max_bytes=MAX_GPX_BYTES, label="GPX")


async def _save_photos(photos: Iterable[UploadFile], workspace: Workspace) -> None:
    saved = 0
    for n, upload in enumerate(photos, start=1):
        if not upload.filename:
            continue
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_PHOTO_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported photo format: {upload.filename!r} "
                    f"(allowed: {sorted(ALLOWED_PHOTO_SUFFIXES)})"
                ),
            )
        if saved >= MAX_PHOTOS_PER_HIKE:
            raise HTTPException(
                status_code=400,
                detail=f"At most {MAX_PHOTOS_PER_HIKE} photos per hike",
            )
        target = workspace.photos_dir / f"{n:03d}{suffix}"
        await _stream_to_disk(upload, target, max_bytes=MAX_PHOTO_BYTES, label="Photo")
        saved += 1
    if saved == 0:
        raise HTTPException(status_code=400, detail="At least one photo is required")


async def _stream_to_disk(upload: UploadFile, target: Path, *, max_bytes: int, label: str) -> None:
    """Stream ``upload`` to ``target``, refusing anything over ``max_bytes``.

    Streaming keeps memory bounded for the carousel-sized photo case.
    The size check runs incrementally so a malicious caller cannot
    exhaust disk before we notice.
    """
    written = 0
    chunk_size = 1024 * 1024
    with target.open("wb") as fh:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"{label} exceeds {max_bytes // (1024 * 1024)} MB",
                )
            fh.write(chunk)
    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"{label} upload was empty")
    await upload.close()


# ── dependency accessors ─────────────────────────────────────────────────────


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _client_factory(request: Request) -> Callable[[], AnthropicClient]:
    factory: Callable[[], AnthropicClient] = request.app.state.client_factory
    return factory


def _ledger_client_factory(request: Request) -> Callable[[], AnthropicClient]:
    """Resolve the ledger-extractor factory injected by :func:`create_app`.

    Mirrors :func:`_client_factory` for the Phase 2 (ADR-009) extractor
    pass — separate factory so each pass's model can be overridden
    independently.
    """
    factory: Callable[[], AnthropicClient] = request.app.state.ledger_client_factory
    return factory


def _vision_client_factory(request: Request) -> Callable[[], AnthropicClient]:
    """Resolve the vision-describer factory injected by :func:`create_app`.

    Mirrors :func:`_client_factory` for the Phase 3 (ADR-010) per-photo
    vision pass — separate factory so the vision model can be swapped
    or disabled independently of writer / ledger.
    """
    factory: Callable[[], AnthropicClient] = request.app.state.vision_client_factory
    return factory


# ── SSE helpers ──────────────────────────────────────────────────────────────


def _sse_event(name: str, data: dict[str, object]) -> bytes:
    """Encode a Server-Sent Event with a named event type and JSON payload.

    The wire format is::

        event: <name>
        data: <json>
        \n

    The trailing blank line is what tells the browser this event is
    complete. We always JSON-encode the payload so the front-end can
    parse it with one ``JSON.parse(e.data)`` call.
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n".encode()


# ── SVG track helpers ────────────────────────────────────────────────────────


# The mini-route SVG drawn in the track-loaded card. Geometry mirrors
# the design proposal's BPMiniTrack — 220 by 110 viewBox with 8px padding.
_TRACK_VIEWBOX_W: Final[int] = 220
_TRACK_VIEWBOX_H: Final[int] = 110
_TRACK_VIEWBOX_PAD: Final[int] = 8
_TRACK_MAX_POINTS: Final[int] = 80


def _track_svg_from_waypoints(
    waypoints: Sequence[object],
) -> tuple[str, list[tuple[float, float]]]:
    """Project (lon, lat) waypoints onto the mini-route SVG box.

    Returns ``(svg_d, endpoints)`` where ``svg_d`` is the path's ``d``
    attribute and ``endpoints`` is the two-point list ``[start, end]``
    used to draw the start (filled) and finish (hollow) markers.

    Returns ``("", [])`` if there are fewer than two waypoints — the
    template renders no SVG in that case.
    """
    if not waypoints or len(waypoints) < 2:
        return "", []
    xs = [getattr(w, "lon", 0.0) for w in waypoints]
    ys = [-getattr(w, "lat", 0.0) for w in waypoints]  # negate so north is up
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0
    inner_w = _TRACK_VIEWBOX_W - 2 * _TRACK_VIEWBOX_PAD
    inner_h = _TRACK_VIEWBOX_H - 2 * _TRACK_VIEWBOX_PAD
    step = max(1, len(waypoints) // _TRACK_MAX_POINTS)
    sampled = list(zip(xs[::step], ys[::step], strict=False))
    if (xs[-1], ys[-1]) != sampled[-1]:
        sampled.append((xs[-1], ys[-1]))
    points: list[tuple[float, float]] = [
        (
            _TRACK_VIEWBOX_PAD + (x - min_x) / span_x * inner_w,
            _TRACK_VIEWBOX_PAD + (y - min_y) / span_y * inner_h,
        )
        for x, y in sampled
    ]
    svg_d = " ".join(
        ("M" if i == 0 else "L") + f"{x:.2f},{y:.2f}" for i, (x, y) in enumerate(points)
    )
    return svg_d, [points[0], points[-1]]


# ── counter ──────────────────────────────────────────────────────────────────


def _bump_counter() -> int:
    """Increment the anonymous request counter and return the new value."""
    global _request_counter
    with _request_counter_lock:
        _request_counter += 1
        return _request_counter


def request_counter_value() -> int:
    """Read the counter — used by tests and (eventually) admin tooling."""
    with _request_counter_lock:
        return _request_counter


def reset_request_counter() -> None:
    """Reset the in-memory counter. Test-only escape hatch."""
    global _request_counter
    with _request_counter_lock:
        _request_counter = 0


# Re-export for tests/integrations.
__all__ = [
    "ALLOWED_PHOTO_SUFFIXES",
    "MAX_GPX_BYTES",
    "MAX_PHOTOS_PER_HIKE",
    "MAX_PHOTO_BYTES",
    "request_counter_value",
    "reset_request_counter",
    "router",
]
