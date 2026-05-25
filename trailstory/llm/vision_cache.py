"""Per-photo cache for vision-derived :class:`PhotoDescription` outputs.

Phase 3.1 / ADR-012. The Phase 3 vision describer calls Claude vision once
per photo on every render; for the click-through dev loop and any re-run
on unchanged inputs, those calls are redundant. This module skips them by
keying on a SHA-256 of ``(photo bytes, vision model identifier)``.

Mirrors :mod:`trailstory.llm.cache` deliberately: same on-disk layout
(``~/.cache/trailstory/vision/<key>.json``), same fail-soft semantics
(OS errors and validation drift return ``None`` rather than raising), same
schema-version check (the model has ``schema_version`` baked in so future
shape changes auto-invalidate stale entries). The vision cache lives in a
separate directory so a clean of one does not blow away the other.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from trailstory.models import PhotoDescription

logger = logging.getLogger(__name__)

# Override target for tests; falls back to ~/.cache/trailstory/vision.
_CACHE_DIR_ENV_VAR = "TRAILSTORY_VISION_CACHE_DIR"


def cache_key(photo_path: Path, vision_model: str) -> str:
    """Stable SHA-256 hex digest for a single photo + vision model pair.

    The bytes of the photo file are the only input that changes the
    description; the model identifier is folded in so swapping vision
    models invalidates entries cleanly (a Haiku-4.5 description should
    not satisfy a Sonnet-4 request even if the photo bytes match).

    Args:
        photo_path: Path to the image being described.
        vision_model: Identifier for the model that would produce the
            description (typically ``Settings.vision_model``).

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    payload = {
        "photo_sha256": _sha256_file(photo_path),
        "vision_model": vision_model,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get(key: str) -> PhotoDescription | None:
    """Return a cached :class:`PhotoDescription` for ``key``, or ``None`` on miss.

    Returns ``None`` when the file is missing, unreadable, not JSON, or
    does not validate against the current :class:`PhotoDescription`
    schema. Schema validation failures are logged but not raised — a
    future shape evolution silently invalidates stale entries.
    """
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("vision cache read failed for %s: %s", key, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("vision cache payload for %s is not a JSON object; ignoring", key)
        return None

    try:
        return PhotoDescription.model_validate(data)
    except ValidationError as exc:
        logger.warning("vision cache entry %s did not validate: %s", key, exc)
        return None


def put(key: str, description: PhotoDescription) -> None:
    """Write ``description`` to the cache under ``key``.

    Best-effort: OS-level errors (full disk, permission denied) are
    logged and swallowed so a cache failure cannot break a successful
    describer run.
    """
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps(description.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("vision cache write failed for %s: %s", key, exc)


# -- internal helpers ---------------------------------------------------------


def _cache_dir() -> Path:
    override = os.environ.get(_CACHE_DIR_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".cache" / "trailstory" / "vision"


def _cache_path(key: str) -> Path:
    return _cache_dir() / f"{key}.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
