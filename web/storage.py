"""Tmp-directory lifecycle for the web builder.

Every web request that runs the pipeline lands in its own
``{root}/{slug}/`` directory. The slug is a short URL-safe id derived
from a fresh UUID — there is no DB, the slug *is* the handle. The
directory has a fixed layout::

    {root}/{slug}/
      input/        raw upload — GPX + photos as the user sent them
      output/       rendered .html (and optionally a carousel/ subdir)

Lifecycle (privacy-first, see ``/privacy``):

* ``create_workspace`` mints a slug and lays out the dirs.
* ``input/`` is wiped as soon as the response is sent (the pipeline has
  already produced the embedded base64 HTML by then; the raw GPX +
  photos are no longer needed).
* ``output/`` is kept for ``RETENTION_SECONDS`` (default 30 minutes) so
  the user can re-share or generate a carousel. After that, a
  background sweep deletes the whole workspace.

The sweep is a simple "scan all workspaces, delete the expired ones"
pass triggered on a timer by ``web.app``. A real scheduler would be
nicer but adds infrastructure for no v0 benefit — privacy is enforced
by always-fresh sweeps, not by clever timing.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# 30 minutes is enough for the user to look at the page, share the link,
# and optionally trigger the carousel before the sweeper deletes
# everything. Long enough not to surprise the user, short enough that a
# server reboot does not leave hours of memories on disk.
RETENTION_SECONDS: Final[int] = 30 * 60

# Slug length. UUID4 has 122 bits of entropy; we keep the leading 12 hex
# characters (~48 bits) so URLs stay short while the collision space is
# still vast for a single-tenant v0.
SLUG_HEX_LENGTH: Final[int] = 12

INPUT_SUBDIR: Final[str] = "input"
GPX_SUBDIR: Final[str] = "gpx"
PHOTOS_SUBDIR: Final[str] = "photos"
# Resized photos live outside ``input/`` so the BackgroundTask cleanup
# (which wipes the raw uploads only) leaves them intact. They are
# already privacy-stripped (no GPS, EXIF orientation baked in) by
# ``trailstory.photos.load_photos``, so keeping them for the retention
# window is safe and unlocks on-demand carousel generation.
RESIZED_SUBDIR: Final[str] = "resized"
OUTPUT_SUBDIR: Final[str] = "output"
STATE_FILENAME: Final[str] = "state.json"


class StorageError(Exception):
    """Raised when a workspace cannot be created or located."""


class Workspace:
    """A single hike's working directory.

    Holds the slug and the resolved subdirectory paths. Created via
    :func:`create_workspace`; never instantiated by the caller directly.
    """

    __slots__ = (
        "gpx_dir",
        "input_dir",
        "output_dir",
        "photos_dir",
        "resized_dir",
        "root",
        "slug",
    )

    def __init__(self, slug: str, root: Path) -> None:
        self.slug = slug
        self.root = root
        self.input_dir = root / INPUT_SUBDIR
        self.gpx_dir = self.input_dir / GPX_SUBDIR
        self.photos_dir = self.input_dir / PHOTOS_SUBDIR
        self.resized_dir = root / RESIZED_SUBDIR
        self.output_dir = root / OUTPUT_SUBDIR

    @property
    def state_path(self) -> Path:
        """Where the persisted ``Memory`` JSON lives."""
        return self.output_dir / STATE_FILENAME

    @property
    def carousel_dir(self) -> Path:
        """Where ``render_instagram_carousel`` writes its slides.

        The renderer's contract is ``output_dir/{slug}/carousel/`` — we
        pass ``self.output_dir`` as the renderer's ``output_dir``, so
        the actual carousel ends up nested under our slug a second time.
        Wrapping that in a property keeps the slide route from needing
        to know.
        """
        return self.output_dir / self.slug / "carousel"


class Storage:
    """Workspace factory + sweeper rooted at a single tmp directory.

    Tests inject a ``tmp_path`` so workspaces live under the test
    sandbox instead of the user's tmp dir. Production passes nothing
    and lands under ``$TMPDIR/trailstory-web/``.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        retention_seconds: int = RETENTION_SECONDS,
    ) -> None:
        self._root = root if root is not None else Path(tempfile.gettempdir()) / "trailstory-web"
        self._root.mkdir(parents=True, exist_ok=True)
        self._retention_seconds = retention_seconds

    @property
    def root(self) -> Path:
        return self._root

    @property
    def retention_seconds(self) -> int:
        return self._retention_seconds

    def create_workspace(self) -> Workspace:
        """Mint a fresh slug and create the directory layout for it.

        Generates a new slug (and retries on the cosmically-improbable
        collision case) so two simultaneous requests cannot stomp on
        the same tree. Subdirs are created up front; the route handler
        writes uploads into ``input/`` and the renderer writes into
        ``output/``.
        """
        for _ in range(8):
            slug = _new_slug()
            ws = Workspace(slug, self._root / slug)
            try:
                ws.root.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            ws.gpx_dir.mkdir(parents=True, exist_ok=True)
            ws.photos_dir.mkdir(parents=True, exist_ok=True)
            ws.resized_dir.mkdir(parents=True, exist_ok=True)
            ws.output_dir.mkdir(parents=True, exist_ok=True)
            logger.info("created workspace %s at %s", slug, ws.root)
            return ws
        # Eight collisions on a 48-bit space is impossible without active
        # interference; surface as a hard error rather than loop forever.
        raise StorageError("could not allocate a unique workspace slug")

    def get_workspace(self, slug: str) -> Workspace | None:
        """Resolve an existing workspace by slug, or ``None`` if it has
        been swept or never existed.

        Validates the slug shape so a malicious caller cannot escape
        the tmp root via ``../``.
        """
        if not _is_valid_slug(slug):
            return None
        ws = Workspace(slug, self._root / slug)
        if not ws.root.is_dir():
            return None
        return ws

    def output_html_path(self, slug: str) -> Path | None:
        """Locate the rendered HTML for a slug, or ``None`` if missing.

        The renderer writes ``{slug}.html`` into ``output/``; we look it
        up here so the route handler doesn't need to know the layout.
        """
        ws = self.get_workspace(slug)
        if ws is None:
            return None
        candidate = ws.output_dir / f"{slug}.html"
        return candidate if candidate.is_file() else None

    def cleanup_inputs(self, ws: Workspace) -> None:
        """Wipe ``input/`` for a workspace.

        Called by the route handler as a ``BackgroundTask`` so the raw
        GPX and photos vanish as soon as the response is sent — by
        then, the renderer has already base64-embedded the photos into
        the output HTML and nothing else needs them.
        """
        if ws.input_dir.exists():
            shutil.rmtree(ws.input_dir, ignore_errors=True)
            logger.info("wiped input dir for workspace %s", ws.slug)

    def delete_workspace(self, ws: Workspace) -> None:
        """Delete a workspace tree entirely. Used by the retention sweep."""
        shutil.rmtree(ws.root, ignore_errors=True)
        logger.info("deleted workspace %s", ws.slug)

    def sweep_expired(self, *, now: float | None = None) -> int:
        """Delete every workspace whose root mtime is older than
        ``retention_seconds``.

        The mtime is bumped each time the renderer writes into the
        directory, so a workspace's clock starts when generation
        completes — the right cue for "user has had this for 30
        minutes."
        """
        now = now if now is not None else time.time()
        cutoff = now - self._retention_seconds
        deleted = 0
        if not self._root.is_dir():
            return 0
        for child in self._root.iterdir():
            if not child.is_dir():
                continue
            try:
                # Use mtime — the renderer's output write bumps it.
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    deleted += 1
            except OSError:
                # Directory disappeared mid-sweep; nothing to do.
                continue
        if deleted:
            logger.info("retention sweep deleted %d expired workspace(s)", deleted)
        return deleted


# ── helpers ──────────────────────────────────────────────────────────────────


def _new_slug() -> str:
    return uuid.uuid4().hex[:SLUG_HEX_LENGTH]


def _is_valid_slug(candidate: str) -> bool:
    """A valid slug is exactly ``SLUG_HEX_LENGTH`` lowercase hex chars.

    The constraint is mostly a path-traversal guard: nothing else can
    appear at ``{root}/{slug}/`` because :func:`_new_slug` only emits
    that shape.
    """
    if len(candidate) != SLUG_HEX_LENGTH:
        return False
    return all(c in "0123456789abcdef" for c in candidate)
