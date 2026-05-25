from __future__ import annotations

import json
import os
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from trailstory.llm.client import (
    AnthropicClient,
    LLMResponseError,
    LLMRetryExhaustedError,
)
from trailstory.models import PhotoDescription, PhotoMeta
from trailstory.photos import (
    PhotoDescriptionError,
    PhotoLoadError,
    describe_photo,
    describe_photos,
    load_photos,
)

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

    # Twelve fixtures, ordered by EXIF DateTimeOriginal — not by filename.
    # File prefixes 06-12 were appended after the original 01-05 set in
    # chore/expand-photo-fixtures, so they sort alphabetically last on
    # disk but interleave in time, exercising the EXIF-based sort path.
    assert len(photos) == 12
    assert [p.path.stem for p in photos] == [
        "01_trailhead",
        "06_meadow",
        "02_forest",
        "07_creek",
        "03_baby_smile",
        "08_lunch",
        "09_baby_carrier",
        "10_clouds",
        "04_ridge",
        "05_summit",
        "11_descent",
        "12_cabin",
    ]
    assert [p.index for p in photos] == list(range(12))
    timestamps = [p.timestamp for p in photos]
    assert all(a < b for a, b in pairwise(timestamps))
    assert photos[0].timestamp == datetime(2025, 8, 15, 9, 5, 12)
    assert photos[-1].timestamp == datetime(2025, 8, 15, 13, 20, 48)


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


def test_load_photos_rejects_pixel_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source whose pixel count exceeds Image.MAX_IMAGE_PIXELS must be
    rejected as a ``PhotoLoadError`` rather than allowed to consume RAM.

    We dial the threshold down with monkeypatch so the fixture can stay tiny
    on disk — a 1000x1000 JPEG is far cheaper to keep around than a synthetic
    bomb large enough to trip the production 200 MP cap.
    """
    src = tmp_path / "src"
    src.mkdir()
    _make_jpeg(src / "huge.jpg", size=(1000, 1000), exif_datetime="2025:01:01 00:00:00")

    # 100_000 < 1000*1000 = 1_000_000 → Pillow raises DecompressionBombError.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100_000)

    with pytest.raises(PhotoLoadError, match="exceeds the maximum pixel budget"):
        load_photos(src, tmp_path / "out")


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


# ── describe_photo + describe_photos (Phase 3 / ADR-010) ────────────────────
#
# Vision describer tests. All paths mock the Anthropic client per CLAUDE.md
# — never call the real API in unit tests. The describer's contract is small
# (one PhotoDescription per photo, retry-once on JSON parse failure, no retry
# on schema-validation failure, soft-fail per-photo at the orchestrator
# layer).


def _valid_description_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "people_visible": ["a hiker in a jacket"],
        "objects_visible": ["a forest path"],
        "location_clues": ["evergreen forest"],
        "season_clues": ["overcast light"],
        "body_language_notes": ["walking forward"],
    }
    base.update(overrides)
    return base


def _valid_description_json(**overrides: object) -> str:
    return json.dumps(_valid_description_dict(**overrides))


def _vision_client(*responses: str | Exception) -> MagicMock:
    """Mocked vision client whose ``complete_vision`` yields each item."""
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-haiku-4-5-vision-test"
    fake.complete_vision.side_effect = list(responses)
    return fake


def _photo(path: Path, *, index: int = 0) -> PhotoMeta:
    return PhotoMeta(path=path, timestamp=datetime(2026, 4, 18, 10, 0, 0), index=index)


def test_describe_photo_happy_path(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    client = _vision_client(_valid_description_json())

    desc = describe_photo(p, client=client)

    assert isinstance(desc, PhotoDescription)
    assert desc.people_visible == ["a hiker in a jacket"]
    assert client.complete_vision.call_count == 1


def test_describe_photo_strips_markdown_fences(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    fenced = "```json\n" + _valid_description_json() + "\n```"
    client = _vision_client(fenced)

    desc = describe_photo(p, client=client)

    assert desc.people_visible == ["a hiker in a jacket"]


def test_describe_photo_retries_once_on_unparseable_first_attempt(
    tmp_path: Path,
) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    client = _vision_client("here is what I see: a forest", _valid_description_json())

    desc = describe_photo(p, client=client)

    assert desc.people_visible == ["a hiker in a jacket"]
    assert client.complete_vision.call_count == 2


def test_describe_photo_raises_after_two_unparseable_attempts(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    client = _vision_client("prose one", "prose two")

    with pytest.raises(PhotoDescriptionError, match="non-JSON output on both"):
        describe_photo(p, client=client)


def test_describe_photo_does_not_retry_on_validation_error(tmp_path: Path) -> None:
    """Schema-validation failures are model bugs; another paid call won't help."""
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    bad = json.dumps({"people_visible": "not a list"})
    client = _vision_client(bad, _valid_description_json())

    with pytest.raises(PhotoDescriptionError, match="schema"):
        describe_photo(p, client=client)
    assert client.complete_vision.call_count == 1


def test_describe_photo_translates_llm_errors(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    client = _vision_client(LLMResponseError("empty"))

    with pytest.raises(PhotoDescriptionError, match="Vision LLM call failed"):
        describe_photo(p, client=client)


def test_describe_photo_translates_retry_exhausted(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    client = _vision_client(LLMRetryExhaustedError("rate-limited 3x"))

    with pytest.raises(PhotoDescriptionError, match="Vision LLM call failed"):
        describe_photo(p, client=client)


def test_describe_photos_attaches_descriptions_in_order(tmp_path: Path) -> None:
    p1 = tmp_path / "01.jpg"
    p2 = tmp_path / "02.jpg"
    _make_jpeg(p1, size=(100, 100))
    _make_jpeg(p2, size=(100, 100))
    photos = [_photo(p1, index=0), _photo(p2, index=1)]
    client = _vision_client(
        _valid_description_json(objects_visible=["lake"]),
        _valid_description_json(objects_visible=["forest"]),
    )

    described = describe_photos(photos, client=client, enabled=True)

    assert len(described) == 2
    assert described[0].description is not None
    assert described[0].description.objects_visible == ["lake"]
    assert described[1].description is not None
    assert described[1].description.objects_visible == ["forest"]


def test_describe_photos_returns_unchanged_when_disabled(tmp_path: Path) -> None:
    """The opt-out path — used when Settings.use_photo_grounding=False."""
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    photos = [_photo(p)]
    client = _vision_client()  # No responses queued; would fail if called.

    result = describe_photos(photos, client=client, enabled=False)

    assert result is photos  # Same list, no copy
    assert client.complete_vision.call_count == 0


def test_describe_photos_skips_failed_photo_and_continues(tmp_path: Path) -> None:
    """A single photo's vision failure must NOT block the whole render —
    the writer simply gets less grounding for that beat."""
    p1 = tmp_path / "01.jpg"
    p2 = tmp_path / "02.jpg"
    _make_jpeg(p1, size=(100, 100))
    _make_jpeg(p2, size=(100, 100))
    photos = [_photo(p1, index=0), _photo(p2, index=1)]
    # First photo: vision LLM errors out hard. Second photo: succeeds.
    client = _vision_client(
        LLMResponseError("boom"),  # photo 1: errors
        _valid_description_json(objects_visible=["lake"]),  # photo 2: ok
    )

    described = describe_photos(photos, client=client, enabled=True)

    assert len(described) == 2
    assert described[0].description is None  # failed photo: no description
    assert described[1].description is not None
    assert described[1].description.objects_visible == ["lake"]


def test_describe_photos_empty_list_returns_empty(tmp_path: Path) -> None:
    client = _vision_client()
    result = describe_photos([], client=client, enabled=True)
    assert result == []
    assert client.complete_vision.call_count == 0


def test_describe_photo_sends_correct_system_prompt(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    client = _vision_client(_valid_description_json())

    describe_photo(p, client=client)

    system = client.complete_vision.call_args.kwargs["system"]
    # The describer is conservative — its prompt names the discipline.
    assert "do not" in system.lower() or "no inferred" in system.lower()
    # JSON output discipline.
    assert "json" in system.lower()


def test_describe_photo_passes_image_path(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    _make_jpeg(p, size=(100, 100))
    client = _vision_client(_valid_description_json())

    describe_photo(p, client=client)

    assert client.complete_vision.call_args.kwargs["image_path"] == p
