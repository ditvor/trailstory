from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener

from trailstory.models import PhotoMeta

register_heif_opener()

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".heic", ".heif"})
DEFAULT_MAX_EDGE = 1800

_EXIF_SUB_IFD_TAG = 0x8769
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
) -> list[PhotoMeta]:
    """Load supported images from photos_dir, sort by capture time, resize.

    Reads every .jpg/.jpeg/.heic/.heif file in photos_dir (non-recursive).
    Sorts by EXIF DateTimeOriginal (then DateTimeDigitized, then DateTime),
    falling back to file mtime when EXIF is absent or malformed. Writes
    resized JPEG copies (longest edge = max_edge, original EXIF preserved
    when present) into resize_dir, and returns one PhotoMeta per image
    with sequential 0-based indices in chronological order.

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
        with Image.open(src) as img:
            timestamp = _extract_timestamp(img, src)
            exif_bytes = img.info.get("exif")
            img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            out_path = resize_dir / f"{src.stem}.jpg"
            rgb = img.convert("RGB")
            if exif_bytes:
                rgb.save(out_path, format="JPEG", quality=90, exif=exif_bytes)
            else:
                rgb.save(out_path, format="JPEG", quality=90)
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
