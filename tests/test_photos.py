from __future__ import annotations

import os
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from trailstory.photos import PhotoLoadError, load_photos

EXIF_ORIENTATION = 0x0112
EXIF_SUB_IFD = 0x8769
EXIF_GPS_IFD = 0x8825
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME = 306

SAMPLE_DIR = Path(__file__).parent / "fixtures" / "sample_photos"


def _make_jpeg(
    path: Path,
    *,
    size: tuple[int, int] = (3000, 2000),
    exif_datetime: str | None = None,
    color: tuple[int, int, int] = (120, 130, 140),
) -> None:
    img = Image.new("RGB", size, color=color)
    if exif_datetime is None:
        img.save(path, format="JPEG", quality=85)
        return
    exif = img.getexif()
    exif[EXIF_DATETIME] = exif_datetime
    sub_ifd = exif.get_ifd(EXIF_SUB_IFD)
    sub_ifd[EXIF_DATETIME_ORIGINAL] = exif_datetime
    img.save(path, format="JPEG", quality=85, exif=exif.tobytes())


def test_load_photos_sorts_by_exif_datetime(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    # Filenames intentionally not in chronological order.
    _make_jpeg(src / "c.jpg", exif_datetime="2025:08:15 12:00:00")
    _make_jpeg(src / "a.jpg", exif_datetime="2025:08:15 09:00:00")
    _make_jpeg(src / "b.jpg", exif_datetime="2025:08:15 10:30:00")

    photos = load_photos(src, tmp_path / "out")

    assert [p.path.stem for p in photos] == ["a", "b", "c"]
    assert [p.index for p in photos] == [0, 1, 2]
    assert photos[0].timestamp == datetime(2025, 8, 15, 9, 0, 0)
    assert photos[2].timestamp == datetime(2025, 8, 15, 12, 0, 0)


def test_load_photos_falls_back_to_mtime_when_exif_missing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    p_old = src / "old.jpg"
    p_new = src / "new.jpg"
    _make_jpeg(p_old)
    _make_jpeg(p_new)

    older = datetime(2024, 1, 1, 8, 0, 0).timestamp()
    newer = datetime(2024, 6, 1, 8, 0, 0).timestamp()
    os.utime(p_old, (older, older))
    os.utime(p_new, (newer, newer))

    photos = load_photos(src, tmp_path / "out")

    assert [p.path.stem for p in photos] == ["old", "new"]
    assert photos[0].timestamp == datetime.fromtimestamp(older)
    assert photos[1].timestamp == datetime.fromtimestamp(newer)


def test_load_photos_resizes_to_max_edge(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _make_jpeg(src / "wide.jpg", size=(4000, 1500), exif_datetime="2025:01:01 00:00:00")
    _make_jpeg(src / "tall.jpg", size=(1500, 4000), exif_datetime="2025:01:01 00:00:01")
    _make_jpeg(src / "small.jpg", size=(800, 600), exif_datetime="2025:01:01 00:00:02")

    photos = load_photos(src, tmp_path / "out", max_edge=1800)

    sizes = {p.path.stem: Image.open(p.path).size for p in photos}
    assert sizes["wide"] == (1800, 675)
    assert sizes["tall"] == (675, 1800)
    # Already smaller than max_edge — should be left untouched.
    assert sizes["small"] == (800, 600)


def test_load_photos_preserves_exif(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _make_jpeg(src / "p.jpg", exif_datetime="2025:08:15 10:30:00")

    [photo] = load_photos(src, tmp_path / "out")

    with Image.open(photo.path) as out:
        exif = out.getexif()
        assert exif.get_ifd(EXIF_SUB_IFD).get(EXIF_DATETIME_ORIGINAL) == "2025:08:15 10:30:00"


def test_load_photos_writes_into_resize_dir(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "nested" / "resized"
    _make_jpeg(src / "x.jpg", exif_datetime="2025:01:01 00:00:00")

    [photo] = load_photos(src, out)

    assert out.is_dir()
    assert photo.path.parent == out
    assert photo.path.suffix == ".jpg"


def test_load_photos_ignores_non_image_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _make_jpeg(src / "ok.jpg", exif_datetime="2025:01:01 00:00:00")
    (src / "notes.txt").write_text("hello")
    (src / "thumbs.db").write_bytes(b"\x00\x01")

    photos = load_photos(src, tmp_path / "out")

    assert len(photos) == 1
    assert photos[0].path.stem == "ok"


def test_load_photos_raises_on_empty_directory(tmp_path: Path) -> None:
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(PhotoLoadError):
        load_photos(src, tmp_path / "out")


def test_load_photos_raises_on_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(PhotoLoadError):
        load_photos(tmp_path / "nope", tmp_path / "out")


def test_load_photos_real_fixtures_chronological(tmp_path: Path) -> None:
    photos = load_photos(SAMPLE_DIR, tmp_path / "out")

    assert len(photos) == 5
    assert [p.path.stem for p in photos] == [
        "01_trailhead",
        "02_forest",
        "03_baby_smile",
        "04_ridge",
        "05_summit",
    ]
    assert [p.index for p in photos] == [0, 1, 2, 3, 4]
    timestamps = [p.timestamp for p in photos]
    assert all(a < b for a, b in pairwise(timestamps))
    assert photos[0].timestamp == datetime(2025, 8, 15, 9, 5, 12)
    assert photos[-1].timestamp == datetime(2025, 8, 15, 12, 40, 9)


def test_load_photos_converts_heic_to_jpeg(tmp_path: Path) -> None:
    photos = load_photos(SAMPLE_DIR, tmp_path / "out")

    heic_origin = next(p for p in photos if p.path.stem == "04_ridge")
    assert heic_origin.path.suffix == ".jpg"
    with Image.open(heic_origin.path) as out:
        assert out.format == "JPEG"
        assert max(out.size) <= 1800


def test_load_photos_handles_malformed_exif_datetime(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    p = src / "bad.jpg"
    _make_jpeg(p, exif_datetime="not a real date")
    fixed = datetime(2024, 3, 14, 9, 26, 53).timestamp()
    os.utime(p, (fixed, fixed))

    [photo] = load_photos(src, tmp_path / "out")

    assert photo.timestamp == datetime.fromtimestamp(fixed)


def test_load_photos_strips_gps_and_applies_exif_transpose(tmp_path: Path) -> None:
    """A photo with GPS coordinates and a non-default orientation tag must be
    written out with no GPS IFD (privacy) and with orientation baked into the
    pixels (so iPhone portraits don't display sideways)."""
    src = tmp_path / "src"
    src.mkdir()
    p = src / "iphone.jpg"

    # 200x100 landscape image. Top-left 50x50 corner is bright red so we can
    # tell where the original "up" was after orientation is applied.
    img = Image.new("RGB", (200, 100), (50, 80, 120))
    for x in range(50):
        for y in range(50):
            img.putpixel((x, y), (255, 0, 0))

    exif = img.getexif()
    # Orientation 6 = "rotate 90° CW for display"; that's how iPhone portraits
    # are stored on disk (sensor is landscape, EXIF says rotate).
    exif[EXIF_ORIENTATION] = 6
    sub_ifd = exif.get_ifd(EXIF_SUB_IFD)
    sub_ifd[EXIF_DATETIME_ORIGINAL] = "2025:08:15 10:30:00"

    gps_ifd = exif.get_ifd(EXIF_GPS_IFD)
    # Munich-ish: 47 deg 33' N, 11 deg 47' E. Real-shaped GPS tags so the
    # test reflects what an actual iPhone photo would carry.
    gps_ifd[1] = "N"
    gps_ifd[2] = (IFDRational(47, 1), IFDRational(33, 1), IFDRational(0, 1))
    gps_ifd[3] = "E"
    gps_ifd[4] = (IFDRational(11, 1), IFDRational(47, 1), IFDRational(0, 1))

    img.save(p, format="JPEG", quality=85, exif=exif.tobytes())

    [photo] = load_photos(src, tmp_path / "out")

    with Image.open(photo.path) as out:
        # (a) GPS is gone — no coordinates leak via the embedded JPEG.
        assert out.getexif().get_ifd(EXIF_GPS_IFD) == {}

        # Camera/timestamp metadata is preserved (only GPS is stripped).
        assert out.getexif().get_ifd(EXIF_SUB_IFD).get(EXIF_DATETIME_ORIGINAL) == (
            "2025:08:15 10:30:00"
        )

        # (b) Orientation has been baked into pixels: the 200x100 landscape
        # source with orientation=6 should now be saved as a 100x200 portrait,
        # which is what a viewer that respects EXIF would already be showing.
        assert out.size == (100, 200)
        # The original top-left red square ended up in the new top-right
        # after a 90 deg CW rotation. Sample well inside that region.
        rgb = out.convert("RGB")
        r, g, b = rgb.getpixel((95, 25))
        assert r > 200 and g < 60 and b < 60
