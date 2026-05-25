"""Run the web builder under uvicorn.

Usage::

    python -m web                       # 0.0.0.0:8000, no reload, real LLM
    python -m web --host 127.0.0.1      # bind to localhost only
    python -m web --reload              # dev: hot-reload on file changes
    python -m web --fake-llm            # deterministic fake LLM, no API calls

The ``--fake-llm`` flag (also ``WEB_FAKE_LLM=1`` in the environment)
swaps the Anthropic client factory for a stub that returns a constant
narrative — useful for clicking through the form / template / output
path without paying for API calls. See ``web.dev`` for details.

The module-level :data:`app` here is what uvicorn imports via the
``web.__main__:app`` target when ``--reload`` is set; otherwise the
factory is invoked once and passed in directly. Tests construct the app
through :func:`web.app.create_app` so this entry point is not exercised
in CI.
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn
from fastapi import FastAPI

from web.app import create_app

_FAKE_LLM_ENV: str = "WEB_FAKE_LLM"


def _build_app() -> FastAPI:
    """Construct the FastAPI app honouring ``WEB_FAKE_LLM``.

    Reads the env var rather than a function argument so this same
    builder works from both ``main()`` (one-shot run) and uvicorn's
    re-import path (``--reload``).
    """
    if os.environ.get(_FAKE_LLM_ENV) == "1":
        # Importing here keeps ``web.dev`` (and its ``unittest.mock``
        # dependency) out of the production import graph.
        from web.dev import (
            banner,
            make_fake_client_factory,
            make_fake_ledger_client_factory,
            make_fake_vision_client_factory,
        )

        # ``Settings`` requires ``ANTHROPIC_API_KEY``. In fake-LLM mode
        # the value is never used — the client factories return
        # MagicMocks — so a placeholder keeps ``load_settings()`` happy
        # without forcing the developer to keep an unused real key in
        # their shell.
        os.environ.setdefault("ANTHROPIC_API_KEY", "sk-dev-fake-llm-mode")
        logging.getLogger(__name__).warning(banner())
        # All three passes (vision + extractor + writer, see ADR-009 +
        # ADR-010) get fake clients so the SSE flow exercises the full
        # three-pass shape without paying for any Anthropic calls.
        return create_app(
            client_factory=make_fake_client_factory(),
            ledger_client_factory=make_fake_ledger_client_factory(),
            vision_client_factory=make_fake_vision_client_factory(),
        )
    return create_app()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m web")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn auto-reload — useful for template iteration.",
    )
    parser.add_argument(
        "--fake-llm",
        action="store_true",
        help=(
            "Use a deterministic fake LLM client (no Anthropic API calls). "
            "Equivalent to setting WEB_FAKE_LLM=1."
        ),
    )
    args = parser.parse_args()

    if args.fake_llm:
        os.environ[_FAKE_LLM_ENV] = "1"

    logging.basicConfig(level=logging.INFO)

    if args.reload:
        # Reload mode requires an import string so the worker process
        # can re-import after each change. ``WEB_FAKE_LLM`` propagates
        # to the child via the inherited environment.
        uvicorn.run(
            "web.__main__:app",
            host=args.host,
            port=args.port,
            reload=True,
        )
    else:
        uvicorn.run(_build_app(), host=args.host, port=args.port)


# Lazily-constructed module-level ``app`` for ``--reload`` / external
# uvicorn invocations like ``uvicorn web.__main__:app``. Importing this
# module at top level (e.g. from tests) does NOT construct the app —
# the import string is resolved by uvicorn after a SIGHUP, at which
# point env vars are set.
def __getattr__(name: str) -> object:
    if name == "app":
        return _build_app()
    raise AttributeError(name)


if __name__ == "__main__":
    main()
