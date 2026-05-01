# syntax=docker/dockerfile:1.7
#
# Trailstory web builder — single-stage image for Fly.io.
#
# We install the package in editable mode on purpose: the HTML renderer
# resolves the memory template via ``Path(__file__).parents[2] / "templates"``
# (see trailstory/renderers/html.py:27), which only works when the
# package source lives next to the top-level templates/ directory at
# runtime. A non-editable install would relocate trailstory/ into
# site-packages and break that path. See CLAUDE.md → "Architecture and
# file map" for the layout this depends on.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Project metadata first so the dependency-install layer is cached on
# any change to source files but invalidated when pyproject.toml moves.
COPY pyproject.toml README.md LICENSE ./
COPY trailstory/ ./trailstory/
COPY web/ ./web/
COPY templates/ ./templates/

# Editable install: dependencies + package, with trailstory/ pointing
# back at /app/trailstory so the renderer's parents[2] still resolves
# to /app/templates.
RUN pip install -e .

# Drop privileges. The retention sweeper writes under $TMPDIR
# (default /tmp) which is world-writeable, so a non-root user is fine.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

# Build identity — set by `make deploy` / `flyctl deploy --build-arg`.
# Surfaced via GET /version so a deploy can be tied back to a commit.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

# Fly injects PORT at runtime; default keeps `docker run` ergonomic.
ENV PORT=8080
EXPOSE 8080

# Shell form so $PORT expands. One uvicorn worker — the LLM call is
# I/O-bound and v0 traffic does not justify multi-worker complexity.
CMD ["sh", "-c", "exec uvicorn web.__main__:app --host 0.0.0.0 --port ${PORT}"]
