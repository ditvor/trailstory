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
        client_factory: Callable returning a configured
            ``AnthropicClient``. Tests inject a factory that returns a
            ``MagicMock`` so the real SDK is never reached.
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
    app.state.generate_limiter = resolved_limiter
    app.state.templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(router)
    return app


# ── internals ────────────────────────────────────────────────────────────────


def _default_client_factory(settings: Settings) -> Callable[[], AnthropicClient]:
    """Build a real ``AnthropicClient`` from settings on each call.

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
