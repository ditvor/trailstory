"""Tests for ``trailstory.llm.cache``.

Cover what the cache promises: stable keys, round-trip, schema-version
invalidation. Cache integration with ``generate_narrative`` and the CLI
is exercised in their own test files.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from trailstory.llm import cache
from trailstory.models import GpxStats, HikeInput, NarrativeOutput, PhotoMeta, Waypoint

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from ``~/.cache/trailstory``."""
    monkeypatch.setenv("TRAILSTORY_CACHE_DIR", str(tmp_path / "cache"))


def _gpx_stats() -> GpxStats:
    return GpxStats(
        distance_km=6.2,
        elevation_gain_m=610,
        duration_min=165,
        start_elev_m=720.0,
        summit_elev_m=1330.0,
        waypoints=[Waypoint(lat=47.55, lon=11.78, ele_m=720.0, time=None)],
    )


def _write_gpx(tmp_path: Path, content: str = "<gpx>track</gpx>") -> Path:
    p = tmp_path / "hike.gpx"
    p.write_text(content, encoding="utf-8")
    return p


def _write_photo(tmp_path: Path, name: str, content: bytes = b"jpeg-bytes") -> PhotoMeta:
    path = tmp_path / name
    path.write_bytes(content)
    return PhotoMeta(path=path, timestamp=datetime(2025, 8, 15, 9), index=0)


def _hike_input(gpx_path: Path) -> HikeInput:
    return HikeInput(
        gpx_path=gpx_path,
        photos_dir=gpx_path.parent,
        seed_text="The fog cleared just as we reached the ridge.",
        baby_name="Mia",
        baby_age_months=5,
        location_name="Bavarian Alps",
    )


def _narrative() -> NarrativeOutput:
    return NarrativeOutput(
        schema_version=1,
        title_en="Above the fog line",
        title_ru="Над линией тумана",
        subtitle_en="A morning above the cloud sea",
        subtitle_ru="Утро над морем облаков",
        paragraphs_en=["First paragraph.", "Second paragraph."],
        paragraphs_ru=["Первый абзац.", "Второй абзац."],
        pull_quote_en="The fog cleared just as we reached the ridge.",
        pull_quote_ru="Туман рассеялся как раз когда мы вышли на хребет.",
        milestone_en="First mountain hike",
        milestone_ru="Первый горный поход",
        selected_photo_indices=[0, 1, 2, 3, 4, 5],
    )


# ── cache_key stability ──────────────────────────────────────────────────────


def test_cache_key_is_stable_for_identical_inputs(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photos = [_write_photo(tmp_path, "a.jpg"), _write_photo(tmp_path, "b.jpg", b"other")]
    inp = _hike_input(gpx)

    k1 = cache.cache_key(inp, _gpx_stats(), photos, "claude-opus-4-7")
    k2 = cache.cache_key(inp, _gpx_stats(), photos, "claude-opus-4-7")

    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_cache_key_is_independent_of_photo_input_order(tmp_path: Path) -> None:
    """Sorting inside cache_key means re-ordering the input list doesn't
    invalidate the cache — same hike, just iterated differently."""
    gpx = _write_gpx(tmp_path)
    p_a = _write_photo(tmp_path, "a.jpg", b"a-bytes")
    p_b = _write_photo(tmp_path, "b.jpg", b"b-bytes")
    inp = _hike_input(gpx)

    forward = cache.cache_key(inp, _gpx_stats(), [p_a, p_b], "claude-opus-4-7")
    reversed_ = cache.cache_key(inp, _gpx_stats(), [p_b, p_a], "claude-opus-4-7")

    assert forward == reversed_


def test_cache_key_changes_when_gpx_bytes_change(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path, "<gpx>v1</gpx>")
    photos = [_write_photo(tmp_path, "a.jpg")]
    inp = _hike_input(gpx)
    before = cache.cache_key(inp, _gpx_stats(), photos, "claude-opus-4-7")

    gpx.write_text("<gpx>v2-different</gpx>", encoding="utf-8")
    after = cache.cache_key(inp, _gpx_stats(), photos, "claude-opus-4-7")

    assert before != after


def test_cache_key_changes_when_photo_bytes_change(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photo = _write_photo(tmp_path, "a.jpg", b"original")
    inp = _hike_input(gpx)
    before = cache.cache_key(inp, _gpx_stats(), [photo], "claude-opus-4-7")

    photo.path.write_bytes(b"re-encoded")
    after = cache.cache_key(inp, _gpx_stats(), [photo], "claude-opus-4-7")

    assert before != after


def test_cache_key_changes_when_seed_text_changes(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photos = [_write_photo(tmp_path, "a.jpg")]
    base = _hike_input(gpx)
    other = base.model_copy(update={"seed_text": "completely different seed"})

    k_base = cache.cache_key(base, _gpx_stats(), photos, "claude-opus-4-7")
    k_other = cache.cache_key(other, _gpx_stats(), photos, "claude-opus-4-7")

    assert k_base != k_other


def test_cache_key_changes_when_baby_name_changes(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photos = [_write_photo(tmp_path, "a.jpg")]
    base = _hike_input(gpx)
    other = base.model_copy(update={"baby_name": "Lev"})

    k_base = cache.cache_key(base, _gpx_stats(), photos, "claude-opus-4-7")
    k_other = cache.cache_key(other, _gpx_stats(), photos, "claude-opus-4-7")

    assert k_base != k_other


def test_cache_key_changes_when_baby_age_changes(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photos = [_write_photo(tmp_path, "a.jpg")]
    base = _hike_input(gpx)
    other = base.model_copy(update={"baby_age_months": 6})

    k_base = cache.cache_key(base, _gpx_stats(), photos, "claude-opus-4-7")
    k_other = cache.cache_key(other, _gpx_stats(), photos, "claude-opus-4-7")

    assert k_base != k_other


def test_cache_key_changes_when_location_changes(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photos = [_write_photo(tmp_path, "a.jpg")]
    base = _hike_input(gpx)
    other = base.model_copy(update={"location_name": "Tegernsee"})

    k_base = cache.cache_key(base, _gpx_stats(), photos, "claude-opus-4-7")
    k_other = cache.cache_key(other, _gpx_stats(), photos, "claude-opus-4-7")

    assert k_base != k_other


def test_cache_key_changes_when_model_changes(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photos = [_write_photo(tmp_path, "a.jpg")]
    inp = _hike_input(gpx)

    opus = cache.cache_key(inp, _gpx_stats(), photos, "claude-opus-4-7")
    sonnet = cache.cache_key(inp, _gpx_stats(), photos, "claude-sonnet-4-6")

    assert opus != sonnet


# ── round-trip ───────────────────────────────────────────────────────────────


def test_get_returns_none_on_miss() -> None:
    assert cache.get("nonexistent-key-0123456789abcdef") is None


def test_put_then_get_round_trips(tmp_path: Path) -> None:
    gpx = _write_gpx(tmp_path)
    photos = [_write_photo(tmp_path, "a.jpg")]
    key = cache.cache_key(_hike_input(gpx), _gpx_stats(), photos, "claude-opus-4-7")

    cache.put(key, _narrative())
    loaded = cache.get(key)

    assert loaded is not None
    assert loaded == _narrative()


def test_put_creates_cache_directory_on_first_use(tmp_path: Path) -> None:
    """``~/.cache/trailstory/narratives/`` may not exist on fresh installs."""
    cache_dir = tmp_path / "cache"
    assert not cache_dir.exists()

    cache.put("abc", _narrative())

    assert cache_dir.is_dir()
    assert (cache_dir / "abc.json").is_file()


# ── schema-version invalidation ──────────────────────────────────────────────


def test_get_returns_none_when_schema_version_differs(tmp_path: Path) -> None:
    """A cached entry from a previous schema version must be ignored —
    the on-disk shape may have fields that no longer exist or be missing
    fields that are now required."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    payload = _narrative().model_dump(mode="json")
    payload["schema_version"] = 999  # arbitrary, future value
    (cache_dir / "stale.json").write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get("stale") is None


def test_get_returns_none_when_schema_version_missing(tmp_path: Path) -> None:
    """Pre-versioning cache entries (no schema_version key at all) must be
    treated as stale, not silently accepted via the field default."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    payload = _narrative().model_dump(mode="json")
    del payload["schema_version"]
    (cache_dir / "preversion.json").write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get("preversion") is None


def test_get_returns_none_on_corrupt_json(tmp_path: Path) -> None:
    """A truncated or otherwise malformed cache file must not crash —
    treat it like a miss so the next call regenerates."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "corrupt.json").write_text("{not valid json", encoding="utf-8")

    assert cache.get("corrupt") is None


def test_get_returns_none_when_payload_is_not_a_json_object(tmp_path: Path) -> None:
    """A JSON list or scalar in the cache file is malformed for our shape."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "list.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert cache.get("list") is None


def test_get_returns_none_when_payload_missing_required_field(tmp_path: Path) -> None:
    """Schema validation must catch shape drift even when the version matches."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    payload = _narrative().model_dump(mode="json")
    del payload["title_en"]
    (cache_dir / "broken.json").write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get("broken") is None
