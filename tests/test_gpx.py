from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from trailstory.gpx import (
    GpxParseError,
    correlate_photos_to_track,
    elevation_profile,
    parse_gpx,
)
from trailstory.models import PhotoMeta, TrackShape

FIXTURE = Path(__file__).parent / "fixtures" / "sample.gpx"


def test_parse_gpx_returns_expected_stats() -> None:
    stats = parse_gpx(FIXTURE)

    assert stats.distance_km > 0
    assert stats.distance_km < 10  # sanity: sample fixture is a few km
    assert stats.elevation_gain_m > 500
    assert stats.duration_min > 0
    assert stats.start_elev_m == pytest.approx(720.0, abs=1.0)
    assert stats.summit_elev_m == pytest.approx(1330.0, abs=1.0)
    assert len(stats.waypoints) == 8
    assert stats.waypoints[0].lat == pytest.approx(47.5580)
    assert stats.waypoints[-1].ele_m == pytest.approx(1330.0)


def test_parse_gpx_raises_on_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.gpx"
    with pytest.raises(FileNotFoundError):
        parse_gpx(missing)


def test_parse_gpx_raises_on_empty_track(tmp_path: Path) -> None:
    empty = tmp_path / "empty.gpx"
    empty.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
        "<trk><trkseg></trkseg></trk></gpx>"
    )
    with pytest.raises(GpxParseError):
        parse_gpx(empty)


def test_elevation_profile_shape() -> None:
    stats = parse_gpx(FIXTURE)
    profile = elevation_profile(stats, n=20)

    assert len(profile) == 20
    xs = [x for x, _ in profile]
    ys = [y for _, y in profile]

    assert xs[0] == 0.0
    assert xs[-1] == 1.0
    assert all(a <= b for a, b in pairwise(xs))  # monotonically increasing
    assert all(0.0 <= y <= 1.0 for y in ys)

    # Fixture is strictly ascending in elevation, so start ~0 and end ~1.
    assert ys[0] == pytest.approx(0.0, abs=0.05)
    assert ys[-1] == pytest.approx(1.0, abs=0.05)


def test_elevation_profile_rejects_small_n() -> None:
    stats = parse_gpx(FIXTURE)
    with pytest.raises(ValueError):
        elevation_profile(stats, n=1)


# ── edge cases ───────────────────────────────────────────────────────────────


def _write_gpx(path: Path, body: str) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><trkseg>{body}</trkseg></trk></gpx>"
    )
    return path


def test_parse_gpx_handles_single_waypoint(tmp_path: Path) -> None:
    """A one-trkpt GPX is a degenerate but valid file. ``parse_gpx`` must not
    raise, and ``elevation_profile`` must return the constant fallback used
    when there is no real profile to interpolate."""
    gpx = _write_gpx(
        tmp_path / "single.gpx",
        '<trkpt lat="47.5" lon="11.7"><ele>500.0</ele></trkpt>',
    )
    stats = parse_gpx(gpx)
    assert len(stats.waypoints) == 1
    assert elevation_profile(stats, n=20) == [(0.0, 0.5), (1.0, 0.5)]


def test_elevation_profile_all_equal_elevations(tmp_path: Path) -> None:
    """A flat track must produce the y=0.5 baseline at every sample point."""
    gpx = _write_gpx(
        tmp_path / "flat.gpx",
        '<trkpt lat="47.5" lon="11.7"><ele>500.0</ele></trkpt>'
        '<trkpt lat="47.51" lon="11.71"><ele>500.0</ele></trkpt>'
        '<trkpt lat="47.52" lon="11.72"><ele>500.0</ele></trkpt>'
        '<trkpt lat="47.53" lon="11.73"><ele>500.0</ele></trkpt>',
    )
    stats = parse_gpx(gpx)
    profile = elevation_profile(stats, n=20)
    assert len(profile) == 20
    assert all(y == 0.5 for _, y in profile)


def test_parse_gpx_with_zero_moving_time(tmp_path: Path) -> None:
    """A GPX with no trkpt timestamps yields zero moving_time. parse_gpx must
    not raise and duration_min must come out as 0."""
    gpx = _write_gpx(
        tmp_path / "no_times.gpx",
        '<trkpt lat="47.5" lon="11.7"><ele>500.0</ele></trkpt>'
        '<trkpt lat="47.51" lon="11.71"><ele>520.0</ele></trkpt>',
    )
    stats = parse_gpx(gpx)
    assert stats.duration_min == 0


# ── ADR-015 — deterministic ledger expansion ─────────────────────────────────


def _write_gpx_with_times(
    path: Path,
    points: list[tuple[float, float, float, str]],
    *,
    name: str | None = None,
) -> Path:
    """Write a GPX file with timestamps and optionally a track-level <name>."""
    body = ""
    for lat, lon, ele, t in points:
        body += f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele><time>{t}</time></trkpt>'
    name_tag = f"<name>{name}</name>" if name else ""
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk>{name_tag}<trkseg>{body}</trkseg></trk></gpx>"
    )
    return path


def test_parse_gpx_includes_elevation_loss(tmp_path: Path) -> None:
    """ADR-015: elevation_loss_m is populated from gpxpy's downhill total
    and is non-zero for tracks that descend.

    gpxpy applies a smoothing filter to absorb GPS elevation jitter, so
    we don't pin exact numbers — we assert presence and rough range.
    """
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.501, 11.701, 900.0, "2025-08-15T09:10:00Z"),
        (47.502, 11.702, 850.0, "2025-08-15T09:20:00Z"),
        (47.503, 11.703, 700.0, "2025-08-15T09:30:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "rolling.gpx", pts)
    stats = parse_gpx(gpx)
    # Real gain ≈ 200m, real loss ≈ 200m — smoothed values land in this band.
    assert 100.0 < stats.elevation_gain_m < 250.0
    assert 100.0 < stats.elevation_loss_m < 250.0


def test_parse_gpx_flat_track_has_zero_loss(tmp_path: Path) -> None:
    """A flat track records zero descent."""
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.501, 11.701, 700.0, "2025-08-15T09:10:00Z"),
        (47.502, 11.702, 700.0, "2025-08-15T09:20:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "flat.gpx", pts)
    stats = parse_gpx(gpx)
    assert stats.elevation_loss_m == 0.0
    assert stats.elevation_gain_m == 0.0


def test_parse_gpx_reads_track_name(tmp_path: Path) -> None:
    """ADR-015: GPX <name> tag becomes GpxStats.track_name."""
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.501, 11.701, 720.0, "2025-08-15T09:10:00Z"),
    ]
    gpx = _write_gpx_with_times(
        tmp_path / "named.gpx",
        pts,
        name="Wallberg via Setzberg",
    )
    stats = parse_gpx(gpx)
    assert stats.track_name == "Wallberg via Setzberg"


def test_parse_gpx_track_name_none_when_absent(tmp_path: Path) -> None:
    """ADR-015: tracks without a <name> tag get track_name=None."""
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.501, 11.701, 720.0, "2025-08-15T09:10:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "unnamed.gpx", pts)
    stats = parse_gpx(gpx)
    assert stats.track_name is None


def test_classify_track_shape_point_to_point(tmp_path: Path) -> None:
    """A straight track A→B (no return) → point_to_point."""
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.510, 11.700, 750.0, "2025-08-15T09:30:00Z"),
        (47.520, 11.700, 800.0, "2025-08-15T10:00:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "point.gpx", pts)
    stats = parse_gpx(gpx)
    assert stats.track_shape == TrackShape.point_to_point


def test_classify_track_shape_out_and_back(tmp_path: Path) -> None:
    """A→B→A along the same path → out_and_back."""
    # ~1 km out along a meridian, then back. bbox = 1 km x 0; total = 2 km.
    # Ratio = 1.0 → out-and-back.
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.502, 11.700, 720.0, "2025-08-15T09:05:00Z"),
        (47.504, 11.700, 740.0, "2025-08-15T09:10:00Z"),
        (47.506, 11.700, 760.0, "2025-08-15T09:15:00Z"),
        (47.508, 11.700, 780.0, "2025-08-15T09:20:00Z"),
        (47.510, 11.700, 800.0, "2025-08-15T09:25:00Z"),
        # Turn around
        (47.508, 11.700, 780.0, "2025-08-15T09:30:00Z"),
        (47.506, 11.700, 760.0, "2025-08-15T09:35:00Z"),
        (47.504, 11.700, 740.0, "2025-08-15T09:40:00Z"),
        (47.502, 11.700, 720.0, "2025-08-15T09:45:00Z"),
        (47.500, 11.700, 700.0, "2025-08-15T09:50:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "outback.gpx", pts)
    stats = parse_gpx(gpx)
    assert stats.track_shape == TrackShape.out_and_back


def test_classify_track_shape_loop(tmp_path: Path) -> None:
    """A square loop with start = end → loop (not out_and_back).

    The bbox covers ~1 km x 1 km and the perimeter is ~4 km, so the
    ratio is ~0.5 — well below the out-and-back threshold.
    """
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.500, 11.702, 700.0, "2025-08-15T09:10:00Z"),
        (47.500, 11.704, 700.0, "2025-08-15T09:20:00Z"),
        (47.500, 11.706, 700.0, "2025-08-15T09:30:00Z"),
        (47.502, 11.706, 700.0, "2025-08-15T09:40:00Z"),
        (47.504, 11.706, 700.0, "2025-08-15T09:50:00Z"),
        (47.506, 11.706, 700.0, "2025-08-15T10:00:00Z"),
        (47.506, 11.704, 700.0, "2025-08-15T10:10:00Z"),
        (47.506, 11.702, 700.0, "2025-08-15T10:20:00Z"),
        (47.506, 11.700, 700.0, "2025-08-15T10:30:00Z"),
        (47.504, 11.700, 700.0, "2025-08-15T10:40:00Z"),
        (47.502, 11.700, 700.0, "2025-08-15T10:50:00Z"),
        (47.500, 11.700, 700.0, "2025-08-15T11:00:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "loop.gpx", pts)
    stats = parse_gpx(gpx)
    assert stats.track_shape == TrackShape.loop


def test_detect_pauses_finds_long_stop(tmp_path: Path) -> None:
    """A cluster of waypoints at the same location for ≥ 5 min becomes
    a single Pause; the surrounding moving segments do not.
    """
    # 1 moving segment, then 7 min of zero motion, then 2 moving segments.
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:00:10Z"),
        # 7-minute pause at the same lat/lon/ele.
        (47.501, 11.701, 705.0, "2025-08-15T09:01:10Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:02:10Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:03:10Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:04:10Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:05:10Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:06:10Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:07:10Z"),
        # Resume movement.
        (47.502, 11.702, 710.0, "2025-08-15T09:07:20Z"),
        (47.503, 11.703, 715.0, "2025-08-15T09:07:30Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "lunch.gpx", pts)
    stats = parse_gpx(gpx)
    assert len(stats.pauses) == 1
    pause = stats.pauses[0]
    assert pause.duration_min >= 5
    assert pause.lat == pytest.approx(47.501, abs=1e-4)
    assert pause.lon == pytest.approx(11.701, abs=1e-4)
    assert pause.ele_m == pytest.approx(705.0, abs=1.0)


def test_detect_pauses_drops_short_stops(tmp_path: Path) -> None:
    """A < 5-minute stationary cluster is treated as GPS noise and dropped."""
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:00:10Z"),
        # 2-minute "stop" — should be filtered out.
        (47.501, 11.701, 705.0, "2025-08-15T09:01:10Z"),
        (47.501, 11.701, 705.0, "2025-08-15T09:02:10Z"),
        (47.502, 11.702, 710.0, "2025-08-15T09:02:20Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "short.gpx", pts)
    stats = parse_gpx(gpx)
    assert stats.pauses == []


def test_detect_pauses_handles_no_timestamps(tmp_path: Path) -> None:
    """A GPX without timestamps yields no pauses (pauses cannot be measured)."""
    gpx = _write_gpx(
        tmp_path / "no_time.gpx",
        '<trkpt lat="47.5" lon="11.7"><ele>500.0</ele></trkpt>'
        '<trkpt lat="47.501" lon="11.701"><ele>505.0</ele></trkpt>'
        '<trkpt lat="47.501" lon="11.701"><ele>505.0</ele></trkpt>'
        '<trkpt lat="47.502" lon="11.702"><ele>510.0</ele></trkpt>',
    )
    stats = parse_gpx(gpx)
    assert stats.pauses == []


def test_correlate_photos_to_track_uses_gps_when_available(tmp_path: Path) -> None:
    """ADR-015: photos with EXIF GPS are matched by great-circle distance
    to the nearest waypoint, not by timestamp.
    """
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.510, 11.700, 750.0, "2025-08-15T09:30:00Z"),
        (47.520, 11.700, 800.0, "2025-08-15T10:00:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "track.gpx", pts)
    stats = parse_gpx(gpx)

    # Photo's GPS sits on top of the middle waypoint. Its timestamp is
    # nonsense (matches the LAST waypoint) — GPS should still win.
    photos = [
        PhotoMeta(
            path=tmp_path / "fake.jpg",
            timestamp=datetime(2025, 8, 15, 10, 0, 0),
            index=0,
            gps_lat=47.510,
            gps_lon=11.700,
        )
    ]
    positions = correlate_photos_to_track(photos, stats)
    assert len(positions) == 1
    # Cumulative distance to the middle waypoint is ~1.11 km (1° lat ≈ 111 km).
    assert positions[0].km_along_track == pytest.approx(1.11, abs=0.1)
    assert positions[0].ele_m == 750.0


def test_correlate_photos_to_track_falls_back_to_timestamp(tmp_path: Path) -> None:
    """ADR-015: photos without GPS are matched to the nearest timed waypoint."""
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.510, 11.700, 750.0, "2025-08-15T09:30:00Z"),
        (47.520, 11.700, 800.0, "2025-08-15T10:00:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "track.gpx", pts)
    stats = parse_gpx(gpx)

    # Photo at 09:28 — naive UTC. Closest waypoint is the 09:30 one.
    photos = [
        PhotoMeta(
            path=tmp_path / "fake.jpg",
            timestamp=datetime(2025, 8, 15, 9, 28, 0),
            index=0,
        )
    ]
    positions = correlate_photos_to_track(photos, stats)
    assert len(positions) == 1
    assert positions[0].ele_m == 750.0


def test_correlate_photos_to_track_returns_empty_when_no_photos(tmp_path: Path) -> None:
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.510, 11.700, 750.0, "2025-08-15T09:30:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "track.gpx", pts)
    stats = parse_gpx(gpx)

    assert correlate_photos_to_track([], stats) == []


def test_correlate_photos_to_track_skips_photo_with_no_match(tmp_path: Path) -> None:
    """A photo with no GPS, combined with a GPX whose waypoints have no
    timestamps, has no usable matching strategy — the photo is omitted.
    """
    gpx = _write_gpx(
        tmp_path / "no_time.gpx",
        '<trkpt lat="47.5" lon="11.7"><ele>500.0</ele></trkpt>'
        '<trkpt lat="47.51" lon="11.71"><ele>520.0</ele></trkpt>',
    )
    stats = parse_gpx(gpx)
    photos = [
        PhotoMeta(
            path=tmp_path / "fake.jpg",
            timestamp=datetime(2025, 8, 15, 9, 0, 0),
            index=0,
        )
    ]
    assert correlate_photos_to_track(photos, stats) == []


def test_correlate_photos_calibrates_clock_offset_from_gps_photos(tmp_path: Path) -> None:
    """EXIF DateTimeOriginal is local wall-clock; GPX times are UTC. A
    GPS-bearing photo in the same upload anchors the offset (here +2h,
    a Munich summer phone), and the GPS-less photo is matched after
    applying it — instead of landing 2 hours (= a third of the hike)
    off, or being dropped by the max-delta guard.
    """
    pts = [
        (47.500 + i * 0.002, 11.700, 700.0 + i * 10, f"2025-08-15T{8 + i:02d}:00:00Z")
        for i in range(5)
    ]  # waypoints at 08:00..12:00Z
    gpx = _write_gpx_with_times(tmp_path / "track.gpx", pts)
    stats = parse_gpx(gpx)

    photos = [
        # Photo A: GPS pins it to the 10:00Z waypoint, but its EXIF says
        # 12:00 (local CEST wall-clock). Anchor: offset = -2h.
        PhotoMeta(
            path=tmp_path / "a.jpg",
            timestamp=datetime(2025, 8, 15, 12, 0, 0),
            index=0,
            gps_lat=47.504,
            gps_lon=11.700,
        ),
        # Photo B: no GPS, EXIF 13:00 local. Calibrated target 11:00Z —
        # must match the 11:00Z waypoint (ele 730), not the 12:00Z one
        # that a raw naive comparison would pick.
        PhotoMeta(
            path=tmp_path / "b.jpg",
            timestamp=datetime(2025, 8, 15, 13, 0, 0),
            index=1,
        ),
    ]
    positions = correlate_photos_to_track(photos, stats)

    assert len(positions) == 2
    by_index = {p.photo_index: p for p in positions}
    assert by_index[0].ele_m == 720.0  # GPS match: 10:00Z waypoint
    assert by_index[1].ele_m == 730.0  # calibrated timestamp match: 11:00Z waypoint


def test_correlate_photos_omits_uncalibratable_clock_skew(tmp_path: Path) -> None:
    """No GPS anchors + a wall-clock hours outside the track window → the
    photo is omitted (omit-rather-than-guess), not confidently pinned to
    the nearest end of the track.
    """
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T08:00:00Z"),
        (47.510, 11.700, 750.0, "2025-08-15T09:00:00Z"),
        (47.520, 11.700, 800.0, "2025-08-15T10:00:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "track.gpx", pts)
    stats = parse_gpx(gpx)

    photos = [
        PhotoMeta(
            path=tmp_path / "skewed.jpg",
            timestamp=datetime(2025, 8, 15, 13, 30, 0),  # 3.5h past track end
            index=0,
        )
    ]
    assert correlate_photos_to_track(photos, stats) == []


def test_pause_at_km_and_photo_km_clamped_to_distance(tmp_path: Path) -> None:
    """distance_km uses gpxpy's moving distance, which excludes stationary
    GPS jitter; at_km / km_along_track use cumulative point-to-point
    distance, which includes it. On a pause-heavy track the cumulative
    basis overshoots — positions must be clamped so the ledger never
    reports a spot beyond the hike's own length.
    """
    t0 = datetime(2025, 8, 15, 9, 0, 0)
    pts: list[tuple[float, float, float, str]] = []
    lat = 47.500
    minute = 0

    def add(lat_step: float) -> None:
        nonlocal lat, minute
        lat += lat_step
        pts.append(
            (
                round(lat, 6),
                11.700,
                700.0,
                (t0 + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
        minute += 1

    add(0.0)  # start
    for _ in range(4):
        add(0.0009)  # ~100 m/min — moving
    for _ in range(14):
        add(0.000036)  # ~4 m/min — stationary jitter (pause 1)
    for _ in range(4):
        add(0.0009)  # moving again
    for _ in range(14):
        add(0.000036)  # jitter pause 2 at the very end of the track

    gpx = _write_gpx_with_times(tmp_path / "jitter.gpx", pts)
    stats = parse_gpx(gpx)

    assert stats.pauses, "expected at least one detected pause"
    for pause in stats.pauses:
        assert pause.at_km <= stats.distance_km

    # A GPS photo at the final waypoint sits at the cumulative maximum —
    # without the clamp its km_along_track would exceed distance_km.
    photos = [
        PhotoMeta(
            path=tmp_path / "p.jpg",
            timestamp=t0,
            index=0,
            gps_lat=pts[-1][0],
            gps_lon=11.700,
        )
    ]
    positions = correlate_photos_to_track(photos, stats)
    assert len(positions) == 1
    assert positions[0].km_along_track <= stats.distance_km


def test_correlate_photos_to_track_handles_tz_aware_photo(tmp_path: Path) -> None:
    """A photo whose timestamp happens to carry tzinfo (extracted from a
    non-EXIF source somewhere) is still comparable to tz-aware GPX times.
    """
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        (47.510, 11.700, 750.0, "2025-08-15T09:30:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "track.gpx", pts)
    stats = parse_gpx(gpx)

    photos = [
        PhotoMeta(
            path=tmp_path / "fake.jpg",
            timestamp=datetime(2025, 8, 15, 9, 5, 0, tzinfo=UTC),
            index=0,
        )
    ]
    positions = correlate_photos_to_track(photos, stats)
    assert len(positions) == 1
    # 09:05 is closer to 09:00 than to 09:30.
    assert positions[0].ele_m == 700.0


def test_detect_pauses_records_at_km(tmp_path: Path) -> None:
    """A pause records its starting position along the track in km."""
    pts = [
        (47.500, 11.700, 700.0, "2025-08-15T09:00:00Z"),
        # ~110m to next point
        (47.501, 11.700, 705.0, "2025-08-15T09:01:00Z"),
        # ~110m to next point (~220m total) — then pause begins
        (47.502, 11.700, 710.0, "2025-08-15T09:02:00Z"),
        # 6-minute pause at this position
        (47.502, 11.700, 710.0, "2025-08-15T09:03:00Z"),
        (47.502, 11.700, 710.0, "2025-08-15T09:05:00Z"),
        (47.502, 11.700, 710.0, "2025-08-15T09:08:00Z"),
        (47.503, 11.700, 715.0, "2025-08-15T09:09:00Z"),
    ]
    gpx = _write_gpx_with_times(tmp_path / "midhike.gpx", pts)
    stats = parse_gpx(gpx)
    assert len(stats.pauses) == 1
    # Pause begins around the 0.22 km mark (~220m in).
    assert stats.pauses[0].at_km == pytest.approx(0.22, abs=0.05)
