"""Regenerate the synthetic photo fixtures used by tests and `make generate`.

Run from the repo root:

    python scripts/generate_sample_photos.py

Produces twelve small images in tests/fixtures/sample_photos/ with EXIF
DateTimeOriginal values that mimic a roughly four-hour hike on 2025-08-15.
Eleven are JPEGs; one is HEIC so the HEIC code path has at least one
real-file test.

The fixture has more than eight photos by design: the narrative prompt
asks the model to pick 6-8 indices, so the eval suite
(`tests/eval/run.py`, `make eval`) can only meaningfully exercise the
`indices_valid` rubric check when the photo pool is comfortably larger
than the upper selection bound.

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

# Filename prefixes are not load-bearing — load_photos() sorts by EXIF
# timestamp, not filename — so the original five keep their existing
# numbering and the seven additions are appended after them in the list.
# Loaded chronological order is determined by the timestamps below, not
# by the order of the entries here.
PHOTOS: list[tuple[str, tuple[int, int], tuple[int, int, int], str]] = [
    ("01_trailhead.jpg", (1200, 800), (90, 130, 80), "2025:08:15 09:05:12"),
    ("02_forest.jpg", (1200, 800), (60, 110, 70), "2025:08:15 09:48:31"),
    ("03_baby_smile.jpg", (800, 1200), (220, 200, 180), "2025:08:15 10:32:04"),
    ("04_ridge.heic", (1200, 800), (130, 160, 200), "2025:08:15 11:51:47"),
    ("05_summit.jpg", (1200, 800), (180, 200, 220), "2025:08:15 12:40:09"),
    ("06_meadow.jpg", (1200, 800), (140, 180, 100), "2025:08:15 09:25:30"),
    ("07_creek.jpg", (1200, 800), (80, 140, 160), "2025:08:15 10:15:18"),
    ("08_lunch.jpg", (1200, 800), (200, 170, 130), "2025:08:15 10:55:09"),
    ("09_baby_carrier.jpg", (800, 1200), (180, 150, 140), "2025:08:15 11:18:33"),
    ("10_clouds.jpg", (1200, 800), (200, 210, 220), "2025:08:15 11:35:42"),
    ("11_descent.jpg", (1200, 800), (110, 130, 90), "2025:08:15 12:55:21"),
    ("12_cabin.jpg", (1200, 800), (160, 110, 90), "2025:08:15 13:20:48"),
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
