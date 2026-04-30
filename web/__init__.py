"""Trailstory web builder.

A FastAPI service that wraps the existing pipeline (``trailstory.gpx`` →
``trailstory.photos`` → ``trailstory.llm`` → ``trailstory.renderers``)
behind a mobile-first form. No accounts, no database — every hike is
generated into a tmp directory keyed by a UUID slug, served for 30
minutes for sharing / carousel export, then deleted.

Public surface is :func:`web.app.create_app` (a FastAPI factory).
``python -m web`` runs it under uvicorn for local development.
"""

from web.app import create_app

__all__ = ["create_app"]
