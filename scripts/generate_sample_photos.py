"""Regenerate the synthetic photo fixtures used by tests and `make generate`.

Run from the repo root:

    python scripts/generate_sample_photos.py

Produces five small images in tests/fixtures/sample_photos/ with EXIF
DateTimeOriginal values that mimic a 4-hour hike on 2025-08-15. Four are
JPEGs; one is HEIC so the HEIC code path has at least one real-file test.

These files are committed to the repo. Re-run only when the fixture set
needs to change.
"""

from __future__ import annotations

from pathlib import Path

import pillow_heif
from PIL import Image

EXIF_SUB_IFD = 0x8769
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME = 306

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_photos"

PHOTOS: list[tuple[str, tuple[int, int], tuple[int, int, int], str]] = [
    ("01_trailhead.jpg", (1200, 800), (90, 130, 80), "2025:08:15 09:05:12"),
    ("02_forest.jpg", (1200, 800), (60, 110, 70), "2025:08:15 09:48:31"),
    ("03_baby_smile.jpg", (800, 1200), (220, 200, 180), "2025:08:15 10:32:04"),
    ("04_ridge.heic", (1200, 800), (130, 160, 200), "2025:08:15 11:51:47"),
    ("05_summit.jpg", (1200, 800), (180, 200, 220), "2025:08:15 12:40:09"),
]


def _build_exif(dt: str) -> bytes:
    img = Image.new("RGB", (1, 1))
    exif = img.getexif()
    exif[EXIF_DATETIME] = dt
    sub = exif.get_ifd(EXIF_SUB_IFD)
    sub[EXIF_DATETIME_ORIGINAL] = dt
    return exif.tobytes()


def main() -> None:
    pillow_heif.register_heif_opener()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size, color, dt in PHOTOS:
        img = Image.new("RGB", size, color=color)
        exif_bytes = _build_exif(dt)
        out = OUTPUT_DIR / name
        if out.suffix.lower() in {".heic", ".heif"}:
            img.save(out, format="HEIF", quality=70, exif=exif_bytes)
        else:
            img.save(out, format="JPEG", quality=80, exif=exif_bytes)
        print(f"  wrote {out.relative_to(OUTPUT_DIR.parent.parent.parent)}")


if __name__ == "__main__":
    main()
