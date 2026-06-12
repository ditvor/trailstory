from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from pydantic import ValidationError

from trailstory.llm import vision_cache
from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.llm.prompts import (
    SYSTEM_PHOTO_DESCRIBER,
    USER_PHOTO_DESCRIBER_RETRY_SUFFIX,
    USER_PHOTO_DESCRIBER_TEMPLATE,
)
from trailstory.models import PhotoDescription, PhotoMeta

logger = logging.getLogger(__name__)

register_heif_opener()

# Reject decompression bombs while staying generous for legitimate photos.
# 200 megapixels is well above any modern phone or full-frame camera sensor
# (the 102 MP medium-format Fujifilm GFX is currently the high-water mark
# for prosumer hardware) but small enough that Pillow refuses to decode the
# obvious bomb shapes (e.g. a 50000x50000 PNG that decompresses from a few
# kilobytes of zlib). Pillow raises Image.DecompressionBombError above this
# threshold, which load_photos wraps in PhotoLoadError.
Image.MAX_IMAGE_PIXELS = 200_000_000

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".heic", ".heif"})
DEFAULT_MAX_EDGE = 1800
DEFAULT_QUALITY = 90

_EXIF_SUB_IFD_TAG = 0x8769
_EXIF_GPS_IFD_TAG = 0x8825
_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME_DIGITIZED = 36868
_EXIF_DATETIME = 306
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"

# GPS sub-IFD tag numbers per the EXIF spec. Reading the four tags below
# is enough to recover a (lat, lon) pair; we deliberately ignore altitude
# (GPX waypoints carry elevation already) and timestamp (the DateTime
# sub-IFD is more reliable and we already read it).
_EXIF_GPS_LAT_REF = 1
_EXIF_GPS_LAT = 2
_EXIF_GPS_LON_REF = 3
_EXIF_GPS_LON = 4


class PhotoLoadError(Exception):
    """Raised when the photo directory is missing or contains no usable images."""


class PhotoDescriptionError(Exception):
    """Raised when the vision describer pass ultimately fails for a photo.

    Phase 3 / ADR-010. Mirrors :class:`LedgerExtractionError`'s policy:
    client-level errors and twice-unparseable responses surface
    immediately; schema-validation failures do not retry. Higher
    layers may decide to treat a single failed photo as a soft error
    (skip + warn) rather than failing the whole render — see
    :func:`describe_photos`.
    """


def load_photos(
    photos_dir: Path,
    resize_dir: Path,
    *,
    max_edge: int = DEFAULT_MAX_EDGE,
    quality: int = DEFAULT_QUALITY,
) -> list[PhotoMeta]:
    """Load supported images from photos_dir, sort by capture time, resize.

    Reads every .jpg/.jpeg/.heic/.heif file in photos_dir (non-recursive).
    Sorts by EXIF DateTimeOriginal (then DateTimeDigitized, then DateTime),
    falling back to file mtime when EXIF is absent or malformed. Writes
    resized JPEG copies (longest edge = max_edge) into resize_dir, with
    EXIF orientation baked into pixels and the GPS sub-IFD removed so the
    rendered HTML cannot leak the photo's location. Camera, lens, and
    timestamp tags are preserved. Returns one PhotoMeta per image with
    sequential 0-based indices in chronological order.

    Photo selection is intentionally out of scope here — that is the LLM's job.
    """
    if not photos_dir.is_dir():
        raise PhotoLoadError(f"{photos_dir} is not a directory")

    sources = sorted(
        p for p in photos_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not sources:
        raise PhotoLoadError(f"No supported photos (jpg/heic) found in {photos_dir}")

    resize_dir.mkdir(parents=True, exist_ok=True)

    items: list[tuple[datetime, Path, float | None, float | None]] = []
    for src in sources:
        try:
            with Image.open(src) as raw:
                # Bake the EXIF orientation into pixels — otherwise iPhone portraits
                # come out sideways in viewers that ignore the tag.
                img = ImageOps.exif_transpose(raw)
                timestamp = _extract_timestamp(img, src)
                exif = img.getexif()
                # ADR-015: read GPS coordinates into Python BEFORE the strip
                # below. The output JPEG still gets its GPS sub-IFD stripped
                # (privacy contract unchanged) — these coordinates only
                # travel through PhotoMeta into the ledger, never into the
                # embedded base64 image.
                gps = _extract_gps(exif)
                # Strip the GPS sub-IFD pointer; HTML output base64-embeds these
                # JPEGs verbatim, so any GPS coordinates would travel with the file.
                # PIL's Exif.tobytes() iterates the private _ifds cache and restores
                # any cached sub-IFD whose tag is missing from the main dict, so we
                # have to clear that cache too — pop alone is not enough.
                exif.pop(_EXIF_GPS_IFD_TAG, None)
                # PIL has no public API to drop a cached sub-IFD; reach into the
                # private cache (this is the only way to make tobytes() drop GPS).
                exif._ifds.pop(_EXIF_GPS_IFD_TAG, None)  # type: ignore[attr-defined]
                img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                out_path = resize_dir / f"{src.stem}.jpg"
                rgb = img.convert("RGB")
                if len(exif):
                    rgb.save(out_path, format="JPEG", quality=quality, exif=exif.tobytes())
                else:
                    rgb.save(out_path, format="JPEG", quality=quality)
        except Image.DecompressionBombError as exc:
            # Hostile or accidental pixel bomb; refuse rather than blow up RAM.
            raise PhotoLoadError(f"{src} exceeds the maximum pixel budget: {exc}") from exc
        gps_lat, gps_lon = (gps[0], gps[1]) if gps is not None else (None, None)
        items.append((timestamp, out_path, gps_lat, gps_lon))

    items.sort(key=lambda t: t[0])
    return [
        PhotoMeta(path=path, timestamp=ts, index=i, gps_lat=lat, gps_lon=lon)
        for i, (ts, path, lat, lon) in enumerate(items)
    ]


def _extract_timestamp(img: Image.Image, path: Path) -> datetime:
    parsed = _exif_datetime_from_image(img)
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(path.stat().st_mtime)


def _exif_datetime_from_image(img: Image.Image) -> datetime | None:
    """Return an EXIF DateTimeOriginal / Digitized / DateTime, or None.

    Pure EXIF read — never falls back to file mtime, so this is safe to
    use against in-memory image data (the web preview path that hands
    around photo bytes without a file path).
    """
    exif = img.getexif()
    if not exif:
        return None
    sub_ifd = exif.get_ifd(_EXIF_SUB_IFD_TAG)
    for tag in (_EXIF_DATETIME_ORIGINAL, _EXIF_DATETIME_DIGITIZED):
        parsed = _parse_exif_datetime(sub_ifd.get(tag))
        if parsed is not None:
            return parsed
    return _parse_exif_datetime(exif.get(_EXIF_DATETIME))


def read_exif_date(data: bytes) -> datetime | None:
    """Read an EXIF date from raw photo bytes without persisting anything.

    Used by the builder's photo-preview endpoint so the AUTO-EXTRACTED
    date chip can be populated with "from photo EXIF" provenance before
    any workspace is created. Returns ``None`` if the image cannot be
    opened or carries no usable date tag.
    """
    import io

    try:
        with Image.open(io.BytesIO(data)) as raw:
            return _exif_datetime_from_image(raw)
    except (OSError, Image.DecompressionBombError):
        # Image.open also raises UnidentifiedImageError on garbage bytes;
        # it's a subclass of OSError, so the bare OSError catch covers it.
        return None


def _parse_exif_datetime(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), _EXIF_DATETIME_FORMAT)
    except ValueError:
        return None


def _extract_gps(exif: Image.Exif) -> tuple[float, float] | None:
    """Return ``(lat, lon)`` in decimal degrees from EXIF, or ``None``.

    Reads the GPS sub-IFD before the strip-on-save step in
    :func:`load_photos`. The four tags required for a position are
    ``GPSLatitudeRef`` (1), ``GPSLatitude`` (2), ``GPSLongitudeRef`` (3),
    ``GPSLongitude`` (4). Lat/lon values are stored as a tuple of three
    ``IFDRational`` values for degrees / minutes / seconds; converting
    to decimal is ``deg + min/60 + sec/3600``, negated when the
    reference is ``S`` or ``W``.

    Returns ``None`` when any of the four tags is missing, the DMS
    tuple is the wrong shape, the values cannot be converted to float,
    or the resulting coordinate falls outside the valid lat/lon range
    (a malformed iPhone Live Photo, for example, has been seen to emit
    ``(0, 0, 0)`` for both axes — semantically "no fix", not a
    coordinate off the coast of Africa). The caller treats ``None`` the
    same as "this photo has no GPS" — soft signal, never an error.
    """
    if not exif:
        return None
    gps_ifd = exif.get_ifd(_EXIF_GPS_IFD_TAG)
    if not gps_ifd:
        return None
    lat_ref = gps_ifd.get(_EXIF_GPS_LAT_REF)
    lat_dms = gps_ifd.get(_EXIF_GPS_LAT)
    lon_ref = gps_ifd.get(_EXIF_GPS_LON_REF)
    lon_dms = gps_ifd.get(_EXIF_GPS_LON)
    if not lat_ref or not lon_ref or lat_dms is None or lon_dms is None:
        return None
    try:
        lat = _dms_to_decimal(lat_dms)
        lon = _dms_to_decimal(lon_dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if str(lat_ref).strip().upper() == "S":
        lat = -lat
    if str(lon_ref).strip().upper() == "W":
        lon = -lon
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    # "no fix" sentinel: both axes report exactly zero. Real coordinates
    # near 0,0 (Gulf of Guinea) are vanishingly rare for hiking photos
    # and not worth the risk of a confused ledger entry.
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def _dms_to_decimal(dms: object) -> float:
    """Convert an EXIF (deg, min, sec) IFDRational tuple to decimal degrees.

    Accepts anything that yields three float-convertible values when
    unpacked; raises ``TypeError`` / ``ValueError`` otherwise (caught
    by :func:`_extract_gps` and translated to "no GPS").
    """
    # mypy can't see through the ``object`` annotation; the runtime
    # ``except`` in the caller covers malformed inputs.
    deg, minutes, seconds = dms  # type: ignore[misc]
    return float(deg) + float(minutes) / 60.0 + float(seconds) / 3600.0  # type: ignore[has-type]


# ── vision describer (Phase 3 / ADR-010) ─────────────────────────────────────
#
# The describer pass calls Claude vision once per photo. The output is a
# typed :class:`PhotoDescription` that gets attached to the photo's
# :class:`PhotoMeta` and feeds into the ledger extractor. Failure of a
# single photo is non-fatal at the orchestrator layer
# (:func:`describe_photos`) so a flaky vision call does not block the
# whole render — the writer simply gets less photo grounding for that
# beat.


def describe_photo(path: Path, *, client: AnthropicClient) -> PhotoDescription:
    """Send one photo through the vision describer and validate the response.

    Args:
        path: Filesystem path to the image. Must be readable; supported
            formats follow ``trailstory.llm.client._MEDIA_TYPES``
            (jpg / jpeg / png / gif / webp, with anything else falling
            through to JPEG).
        client: Anthropic client configured with a vision-capable model
            (typically ``Settings.vision_model``). Injected so tests
            can mock the SDK.

    Returns:
        Validated :class:`PhotoDescription`.

    Raises:
        PhotoDescriptionError: LLM call failed, response did not parse
            as JSON twice in a row, or the parsed JSON did not validate
            against :class:`PhotoDescription`.
    """
    parsed = _vision_call_and_parse(client, path, USER_PHOTO_DESCRIBER_TEMPLATE)
    if parsed is None:
        logger.warning(
            "photo describer response did not parse as JSON; retrying with explicit directive"
        )
        retry_prompt = USER_PHOTO_DESCRIBER_TEMPLATE + USER_PHOTO_DESCRIBER_RETRY_SUFFIX
        parsed = _vision_call_and_parse(client, path, retry_prompt)
        if parsed is None:
            raise PhotoDescriptionError(
                f"Photo describer returned non-JSON output on both attempts for {path.name}"
            )

    try:
        return PhotoDescription.model_validate(parsed)
    except ValidationError as exc:
        raise PhotoDescriptionError(
            f"Photo describer JSON did not match PhotoDescription schema for {path.name}: {exc}"
        ) from exc


def describe_photos(
    photos: list[PhotoMeta],
    *,
    client: AnthropicClient,
    enabled: bool = True,
    concurrency: int = 4,
    use_cache: bool = True,
) -> list[PhotoMeta]:
    """Walk a photo list and attach vision-derived descriptions to each.

    Phase 3.1 / ADR-012 adds an on-disk cache keyed by photo bytes +
    vision model — a re-run on the same photos skips the vision call
    entirely. Phase 3.2 / ADR-013 runs the LLM calls in parallel
    (``concurrency`` workers via ``ThreadPoolExecutor``) so a 6-photo
    hike completes in roughly the wall time of a single photo plus
    coordination overhead. The Anthropic SDK is thread-safe; the GIL
    releases on the HTTP wait, so threads beat a single-threaded loop
    by a wide margin on the I/O-bound describer workload.

    Returns a fresh list of :class:`PhotoMeta` copies (the model is
    frozen, so updates flow via ``model_copy(update={"description": ...})``).
    When ``enabled`` is ``False``, returns the input list unchanged —
    the opt-out path for users / dev modes who want speed and cost over
    photo grounding (per ``Settings.use_photo_grounding``).

    A single photo's failure is **non-fatal**: the function logs a
    warning, leaves that photo's ``description`` as ``None``, and
    continues with the rest. The ledger extractor handles a partial
    description list (missing photos are simply not grounded against);
    failing the whole render because one vision call timed out would be
    a regression in the user's eyes.

    Args:
        photos: The list of photos to describe. Typically the output of
            :func:`load_photos`.
        client: Anthropic client configured with a vision-capable
            model (typically ``Settings.vision_model``).
        enabled: When ``False``, skip every vision call and return the
            input list unchanged. Defaults to ``True``.
        concurrency: Max parallel vision calls. Higher saturates the
            network and the Anthropic rate limit faster; lower keeps
            the burst small. Default ``4`` is a reasonable balance for
            v0 traffic.
        use_cache: When ``True`` (default), look up each photo in the
            on-disk vision cache before calling the model. Tests pass
            ``False`` so they can assert call counts on the mocked
            client.

    Returns:
        A new list of :class:`PhotoMeta` instances, in the same order as
        the input, each with ``description`` set when the vision call
        succeeded (or was served from cache).
    """
    if not enabled:
        return photos
    if not photos:
        return photos

    # Single-photo path: skip the threadpool to keep the call-count
    # semantics obvious in tests and avoid the (tiny) per-thread overhead.
    if len(photos) == 1 or concurrency <= 1:
        return [
            _describe_one_with_cache(photo, client=client, use_cache=use_cache) for photo in photos
        ]

    # Parallel path. ThreadPoolExecutor preserves submission order via
    # `.map`; results land in the same order as `photos`. Per-photo
    # failures are caught inside `_describe_one_with_cache` and surface
    # as a `None` description, never as an exception.
    with ThreadPoolExecutor(max_workers=min(concurrency, len(photos))) as pool:
        return list(
            pool.map(
                lambda photo: _describe_one_with_cache(photo, client=client, use_cache=use_cache),
                photos,
            )
        )


def _describe_one_with_cache(
    photo: PhotoMeta, *, client: AnthropicClient, use_cache: bool
) -> PhotoMeta:
    """Vision-describe one photo, honouring the on-disk cache.

    Cache hit returns the cached PhotoDescription attached to a fresh
    PhotoMeta copy. Cache miss runs ``describe_photo``, writes the
    result back into the cache, and returns the updated copy. A vision
    failure surfaces as a logged warning and the original PhotoMeta
    (with ``description=None``) — same soft-fail contract as the
    pre-cache version.
    """
    if use_cache:
        key = vision_cache.cache_key(photo.path, client.model)
        cached = vision_cache.get(key)
        if cached is not None:
            logger.info("vision cache hit for %s", photo.path.name)
            return photo.model_copy(update={"description": cached})
        logger.info("vision cache miss for %s", photo.path.name)
    try:
        description = describe_photo(photo.path, client=client)
    except PhotoDescriptionError as exc:
        logger.warning(
            "photo describer failed for %s (idx=%d); proceeding without description: %s",
            photo.path.name,
            photo.index,
            exc,
        )
        return photo
    if use_cache:
        vision_cache.put(vision_cache.cache_key(photo.path, client.model), description)
    return photo.model_copy(update={"description": description})


# -- internal helpers ----------------------------------------------------


def _vision_call_and_parse(
    client: AnthropicClient, image_path: Path, prompt: str
) -> dict[str, Any] | None:
    """One vision call + JSON parse. Returns None on parse failure for retry.

    Client-level errors are funnelled into
    :class:`PhotoDescriptionError` immediately because retrying them
    would duplicate the client's own retry policy.
    """
    try:
        raw = client.complete_vision(
            prompt=prompt, system=SYSTEM_PHOTO_DESCRIBER, image_path=image_path
        )
    except (LLMResponseError, LLMRetryExhaustedError) as exc:
        raise PhotoDescriptionError(f"Vision LLM call failed for {image_path.name}: {exc}") from exc

    cleaned = _strip_code_fences(raw)
    try:
        result = json.loads(cleaned)
    except JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _strip_code_fences(text: str) -> str:
    """Remove a leading / trailing markdown code fence if present.

    Mirrors the helper in ``trailstory.llm.narrative`` — kept duplicated
    so the photos module does not import private helpers from the
    narrative orchestrator.
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].rstrip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
