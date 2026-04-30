"""HTTP route handlers for the web builder.

Six endpoints, all stateless from the user's point of view:

* ``GET /``                          — landing page + builder form.
* ``POST /generate``                 — multipart upload; runs the pipeline
                                        and 303-redirects to the memory page.
* ``GET /memory/{slug}``             — serves the rendered HTML.
* ``POST /memory/{slug}/carousel``   — generates the IG carousel on demand.
* ``GET /privacy``                   — plain-language privacy page.
* ``GET /healthz``                   — uptime probe.

Heavy lifting (parse / load / narrative / render) lives in
``web.pipeline``; this module is just request validation, file I/O,
response shaping, and the BackgroundTask that wipes raw uploads after a
successful generate. That keeps the file readable when scanning for
attack surface.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from trailstory.config import Settings
from trailstory.llm.client import AnthropicClient
from web.pipeline import PipelineError, Style, render_carousel, run_pipeline
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
    """Builder form. Mobile-first; everything fits in one column."""
    return _templates(request).TemplateResponse(
        request,
        "landing.html.j2",
        {"styles": [s.value for s in Style], "default_style": Style.default().value},
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


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Uptime probe. Static — does not touch storage or the LLM."""
    return {"status": "ok"}


# ── pipeline ─────────────────────────────────────────────────────────────────


@router.post("/generate")
async def generate(
    request: Request,
    background_tasks: BackgroundTasks,
    description: Annotated[str, Form(min_length=1, max_length=1000)],
    style: Annotated[str, Form()] = Style.default().value,
    location: Annotated[str | None, Form()] = None,
    gpx: UploadFile | None = None,
    photos: list[UploadFile] | None = None,
) -> Response:
    """Run the pipeline against an upload and redirect to the memory page.

    Raises a 4xx if the inputs are missing, oversized, or unsupported;
    a 502 if the LLM call fails. The 303 redirect is what makes the
    "POST then GET" pattern work without resubmitting on refresh.
    """
    if gpx is None or not gpx.filename:
        raise HTTPException(status_code=400, detail="GPX file is required")
    if not photos or all(not p.filename for p in photos):
        raise HTTPException(status_code=400, detail="At least one photo is required")

    try:
        chosen_style = Style(style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown style: {style!r}") from exc

    storage = _storage(request)
    workspace = storage.create_workspace()

    try:
        await _save_gpx(gpx, workspace)
        await _save_photos(photos, workspace)
        client = _client_factory(request)()
        settings = _settings(request)
        memory, _ = run_pipeline(
            workspace,
            description=description,
            style=chosen_style,
            client=client,
            photo_max_edge=settings.photo_max_edge,
            photo_quality=settings.photo_quality,
            location=(location or None),
        )
    except PipelineError as exc:
        # Pipeline failed after we created the workspace; don't leave
        # half-baked state on disk.
        storage.delete_workspace(workspace)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        storage.delete_workspace(workspace)
        raise
    except Exception:
        storage.delete_workspace(workspace)
        raise
    finally:
        # Make sure we always wipe raw uploads even if generation
        # succeeded — the background task is the privacy guarantee.
        background_tasks.add_task(storage.cleanup_inputs, workspace)

    _bump_counter()
    logger.info(
        "generated memory %s (style=%s, photos=%d)",
        workspace.slug,
        chosen_style.value,
        len(memory.selected_photos),
    )
    # 303 because we are switching from POST to GET.
    return RedirectResponse(url=f"/memory/{workspace.slug}", status_code=303)


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
    return FileResponse(target, media_type="image/jpeg")


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
