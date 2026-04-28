"""Content-addressed cache for LLM narrative outputs.

The narrative is the most expensive single step in the pipeline: one Opus
call per hike, paid per token. While iterating on the HTML template or
Instagram carousel renderer, we re-run ``trailstory generate`` against
the same hike inputs many times — the LLM call dominates that loop and
returns essentially identical output every time. This module avoids
those redundant calls.

Design:

* The cache key is a SHA-256 of every input the LLM actually sees,
  including the model name. Same hike + same model → same key → same
  cached narrative. Any byte-level change to the GPX, any photo added /
  removed / re-encoded, any tweak to the seed text — all produce a new
  key.
* Entries are JSON-serialised ``NarrativeOutput`` instances written to
  ``~/.cache/trailstory/narratives/<key>.json``. Plain JSON so they're
  trivially inspectable.
* Schema drift is auto-handled: ``get`` validates the cached payload
  against the current ``NarrativeOutput``. Validation failure (including
  a ``schema_version`` mismatch) returns ``None`` rather than raising,
  so a model-shape evolution silently invalidates stale entries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from trailstory.models import GpxStats, HikeInput, NarrativeOutput, PhotoMeta

logger = logging.getLogger(__name__)

# Override target for tests; falls back to ~/.cache/trailstory/narratives.
_CACHE_DIR_ENV_VAR = "TRAILSTORY_CACHE_DIR"


def cache_key(
    hike_input: HikeInput,
    gpx_stats: GpxStats,
    photos: list[PhotoMeta],
    model: str,
) -> str:
    """Stable SHA-256 hex digest for a narrative-generation request.

    Hashes every input the LLM actually sees, plus the model identifier.
    The GPX is hashed as raw file bytes (the parsed ``GpxStats`` is a
    lossy projection — distance + elevation alone don't uniquely
    identify a track). Photos are identified by ``(basename, sha256 of
    bytes)`` so re-encoded files invalidate the cache while a directory
    rename does not.

    Args:
        hike_input: Source paths and parent-supplied seed/baby info.
        gpx_stats: Unused for hashing — accepted for symmetry with
            ``generate_narrative``'s call site and to make this the one
            place callers can later mix derived stats into the key
            without ripping signatures.
        photos: Loaded photos. Sorted by basename before hashing so the
            order in the input list does not affect the key.
        model: Model identifier (e.g. ``"claude-opus-4-7"``). Different
            models produce different narratives — they get different keys.

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    del gpx_stats  # see docstring; reserved for future use without API churn

    payload: dict[str, object] = {
        "gpx_sha256": _sha256_file(hike_input.gpx_path),
        "photos": sorted((p.path.name, _sha256_file(p.path)) for p in photos),
        "seed_text": hike_input.seed_text,
        "baby_name": hike_input.baby_name,
        "baby_age_months": hike_input.baby_age_months,
        "location_name": hike_input.location_name,
        "model": model,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get(key: str) -> NarrativeOutput | None:
    """Return a cached narrative for ``key``, or ``None`` on miss.

    Returns ``None`` when:

    * the cache file does not exist,
    * the cache file is unreadable or not valid JSON,
    * the JSON does not validate against the current ``NarrativeOutput``
      (this includes a ``schema_version`` mismatch — handled in the same
      validator, so old shapes are auto-invalidated transparently).
    """
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cache read failed for %s: %s", key, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("cache payload for %s is not a JSON object; ignoring", key)
        return None

    cached_version = data.get("schema_version")
    current_version = NarrativeOutput.model_fields["schema_version"].default
    if cached_version != current_version:
        logger.info(
            "cache entry %s has schema_version=%r; current=%r — invalidating",
            key,
            cached_version,
            current_version,
        )
        return None

    try:
        return NarrativeOutput.model_validate(data)
    except ValidationError as exc:
        logger.warning("cache entry %s did not validate: %s", key, exc)
        return None


def put(key: str, narrative: NarrativeOutput) -> None:
    """Write ``narrative`` to the cache under ``key``.

    Creates the cache directory on first use. Writes are best-effort:
    OS-level errors (full disk, permission denied) are logged and
    swallowed so a cache failure cannot break a successful generate run.
    """
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # ``mode="json"`` produces a JSON-friendly dict (Path objects to
        # strings, datetimes to ISO strings, etc.). NarrativeOutput
        # currently has no such fields, but this keeps the cache robust
        # against future field additions.
        path.write_text(
            json.dumps(narrative.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("cache write failed for %s: %s", key, exc)


# -- internal helpers ---------------------------------------------------------


def _cache_dir() -> Path:
    override = os.environ.get(_CACHE_DIR_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".cache" / "trailstory" / "narratives"


def _cache_path(key: str) -> Path:
    return _cache_dir() / f"{key}.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
