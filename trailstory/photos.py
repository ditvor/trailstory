from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from trailstory.models import PhotoMeta

register_heif_opener()

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".heic", ".heif"})
DEFAULT_MAX_EDGE = 1800
DEFAULT_QUALITY = 90

_EXIF_SUB_IFD_TAG = 0x8769
_EXIF_GPS_IFD_TAG = 0x8825
_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME_DIGITIZED = 36868
_EXIF_DATETIME = 306
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"


class PhotoLoadError(Exception):
    """Raised when the photo directory is missing or contains no usable images."""


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

    items: list[tuple[datetime, Path]] = []
    for src in sources:
        with Image.open(src) as raw:
            # Bake the EXIF orientation into pixels — otherwise iPhone portraits
            # come out sideways in viewers that ignore the tag.
            img = ImageOps.exif_transpose(raw)
            timestamp = _extract_timestamp(img, src)
            exif = img.getexif()
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
        items.append((timestamp, out_path))

    items.sort(key=lambda t: t[0])
    return [PhotoMeta(path=path, timestamp=ts, index=i) for i, (ts, path) in enumerate(items)]


def _extract_timestamp(img: Image.Image, path: Path) -> datetime:
    exif = img.getexif()
    if exif:
        sub_ifd = exif.get_ifd(_EXIF_SUB_IFD_TAG)
        for tag in (_EXIF_DATETIME_ORIGINAL, _EXIF_DATETIME_DIGITIZED):
            parsed = _parse_exif_datetime(sub_ifd.get(tag))
            if parsed is not None:
                return parsed
        parsed = _parse_exif_datetime(exif.get(_EXIF_DATETIME))
        if parsed is not None:
            return parsed
    return datetime.fromtimestamp(path.stat().st_mtime)


def _parse_exif_datetime(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), _EXIF_DATETIME_FORMAT)
    except ValueError:
        return None
