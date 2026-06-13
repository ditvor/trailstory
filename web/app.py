"""FastAPI application factory.

Two responsibilities:

1. Wire the dependencies the route handlers read off ``app.state``:
   ``Settings``, ``Storage``, the LLM client factory, and the Jinja2
   template environment for the builder UI. Tests construct the app
   with overrides so the real Anthropic SDK is never called.
2. Schedule the periodic retention sweep (``Storage.sweep_expired``)
   on a background asyncio task started by FastAPI's lifespan hook.

The module exposes :func:`create_app` (the test-friendly factory) and
``app`` (a module-level instance for ``uvicorn web:app``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from trailstory.config import Settings, load_settings
from trailstory.llm.client import AnthropicClient
from trailstory.place import resolve_place_reference
from web.pipeline import PlaceReferenceResolver
from web.ratelimit import GENERATE_LIMIT_PER_HOUR, GENERATE_WINDOW_SECONDS, RateLimiter
from web.routes import router
from web.storage import Storage

logger = logging.getLogger(__name__)

WEB_DIR: Final[Path] = Path(__file__).resolve().parent
TEMPLATE_DIR: Final[Path] = WEB_DIR / "templates"
STATIC_DIR: Final[Path] = WEB_DIR / "static"

# How often the retention sweep runs while the server is up. A few times
# per retention window so a workspace is rarely much more than its
# advertised lifetime overdue.
SWEEP_INTERVAL_SECONDS: Final[int] = 5 * 60


def create_app(
    *,
    settings: Settings | None = None,
    storage: Storage | None = None,
    client_factory: Callable[[], AnthropicClient] | None = None,
    ledger_client_factory: Callable[[], AnthropicClient] | None = None,
    vision_client_factory: Callable[[], AnthropicClient] | None = None,
    place_client_factory: Callable[[], AnthropicClient] | None = None,
    place_reference_resolver: PlaceReferenceResolver | None = None,
    rate_limiter: RateLimiter | None = None,
    enable_sweeper: bool = True,
) -> FastAPI:
    """Build a configured FastAPI app.

    Args:
        settings: Application settings. Defaults to
            :func:`trailstory.config.load_settings`. Tests pass a
            pre-built Settings (with a fake API key) here.
        storage: Pre-built ``Storage``. Tests pass one rooted at a
            ``tmp_path`` so workspaces are scoped to the test run.
        client_factory: Callable returning a configured WRITER
            ``AnthropicClient`` (Opus-class). Tests inject a factory that
            returns a ``MagicMock`` so the real SDK is never reached.
        ledger_client_factory: Callable returning a configured EXTRACTOR
            ``AnthropicClient`` (Haiku-class, per ADR-009). Defaults to
            a factory built from ``Settings.ledger_model``. Tests inject
            a mock factory the same way as ``client_factory``.
        vision_client_factory: Callable returning a configured VISION
            ``AnthropicClient`` (Haiku-class, per ADR-010). Defaults to
            a factory built from ``Settings.vision_model``. Tests inject
            a mock factory the same way as the other two.
        place_client_factory: Callable returning a configured PLACE-stitch
            ``AnthropicClient`` (Haiku-class, per ADR-017). Defaults to a
            factory built from ``Settings.place_model``. Only invoked when
            a request opts into the place block.
        place_reference_resolver: Geocode + Wikipedia resolver for the place
            block (ADR-017). Defaults to
            ``trailstory.place.resolve_place_reference`` (real network).
            The fake-LLM dev mode and tests inject an offline stub so no
            external request is made.
        rate_limiter: Per-IP limiter for ``/generate``. Defaults to a
            sliding-window ``RateLimiter`` sized at
            :data:`web.ratelimit.GENERATE_LIMIT_PER_HOUR`. Tests pass
            a tiny limit so the 429 path is reachable in a few calls.
        enable_sweeper: When ``True`` (default), the retention sweep is
            scheduled on app startup. Tests disable this so they can
            assert sweep behaviour by calling ``Storage.sweep_expired``
            directly without async timing flake.
    """
    resolved_settings = settings if settings is not None else load_settings()
    resolved_storage = storage if storage is not None else Storage()
    resolved_factory = (
        client_factory if client_factory is not None else _default_client_factory(resolved_settings)
    )
    resolved_ledger_factory = (
        ledger_client_factory
        if ledger_client_factory is not None
        else _default_ledger_client_factory(resolved_settings)
    )
    resolved_vision_factory = (
        vision_client_factory
        if vision_client_factory is not None
        else _default_vision_client_factory(resolved_settings)
    )
    resolved_place_factory = (
        place_client_factory
        if place_client_factory is not None
        else _default_place_client_factory(resolved_settings)
    )
    resolved_place_resolver: PlaceReferenceResolver = (
        place_reference_resolver
        if place_reference_resolver is not None
        else resolve_place_reference
    )
    resolved_limiter = (
        rate_limiter
        if rate_limiter is not None
        else RateLimiter(
            limit=GENERATE_LIMIT_PER_HOUR,
            window_seconds=GENERATE_WINDOW_SECONDS,
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        sweeper_task: asyncio.Task[None] | None = None
        if enable_sweeper:
            sweeper_task = asyncio.create_task(_run_sweeper(resolved_storage))
            logger.info(
                "retention sweeper started (interval=%ds, retention=%ds)",
                SWEEP_INTERVAL_SECONDS,
                resolved_storage.retention_seconds,
            )
        try:
            yield
        finally:
            if sweeper_task is not None:
                sweeper_task.cancel()
                try:
                    await sweeper_task
                except (asyncio.CancelledError, Exception):
                    # Cancellation is the expected exit. Anything else
                    # has already been logged inside _run_sweeper.
                    pass

    app = FastAPI(
        title="Trailstory",
        description="Turn a hike into a memory worth sharing.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.storage = resolved_storage
    app.state.client_factory = resolved_factory
    app.state.ledger_client_factory = resolved_ledger_factory
    app.state.vision_client_factory = resolved_vision_factory
    app.state.place_client_factory = resolved_place_factory
    app.state.place_reference_resolver = resolved_place_resolver
    app.state.generate_limiter = resolved_limiter
    app.state.templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(router)
    return app


# ── internals ────────────────────────────────────────────────────────────────


def _default_client_factory(settings: Settings) -> Callable[[], AnthropicClient]:
    """Build a real WRITER ``AnthropicClient`` from settings on each call.

    Returning a fresh instance per request keeps the SDK's internal
    HTTP connection pool scoped tightly; for v0 traffic that overhead
    is negligible.
    """

    def factory() -> AnthropicClient:
        return AnthropicClient(
            settings.anthropic_api_key,
            model=settings.model,
            max_tokens=settings.narrative_max_tokens,
            max_retries=settings.narrative_max_retries,
        )

    return factory


def _default_ledger_client_factory(settings: Settings) -> Callable[[], AnthropicClient]:
    """Build a real EXTRACTOR ``AnthropicClient`` from settings.

    Phase 2 (ADR-009): the ledger pass uses a cheaper, faster model
    (``Settings.ledger_model``) than the writer's Opus. Same per-request
    fresh-instance pattern as :func:`_default_client_factory`.
    """

    def factory() -> AnthropicClient:
        return AnthropicClient(
            settings.anthropic_api_key,
            model=settings.ledger_model,
            max_tokens=settings.narrative_max_tokens,
            max_retries=settings.narrative_max_retries,
        )

    return factory


def _default_vision_client_factory(settings: Settings) -> Callable[[], AnthropicClient]:
    """Build a real VISION ``AnthropicClient`` from settings.

    Phase 3 (ADR-010): the per-photo describer uses
    ``Settings.vision_model`` (Haiku-class by default, must be
    vision-capable). Same per-request fresh-instance pattern as the
    other two factories.
    """

    def factory() -> AnthropicClient:
        return AnthropicClient(
            settings.anthropic_api_key,
            model=settings.vision_model,
            max_tokens=settings.narrative_max_tokens,
            max_retries=settings.narrative_max_retries,
        )

    return factory


def _default_place_client_factory(settings: Settings) -> Callable[[], AnthropicClient]:
    """Build a real PLACE-stitch ``AnthropicClient`` from settings.

    ADR-017: the "about this place" stitch uses ``Settings.place_model``
    (Haiku-class by default — a constrained stitching task). Same
    per-request fresh-instance pattern as the other factories. Only
    actually invoked when a request opts into the place block; building it
    per request is cheap (it just wraps the SDK).
    """

    def factory() -> AnthropicClient:
        return AnthropicClient(
            settings.anthropic_api_key,
            model=settings.place_model,
            max_tokens=settings.narrative_max_tokens,
            max_retries=settings.narrative_max_retries,
        )

    return factory


async def _run_sweeper(storage: Storage) -> None:
    """Run the retention sweep on a fixed cadence until cancelled."""
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            storage.sweep_expired()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Logged but not fatal — a transient OS error must not
            # take the sweeper down for the rest of the process.
            logger.exception("retention sweep failed; will retry next interval")
