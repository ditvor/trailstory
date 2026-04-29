from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from trailstory.gpx import GpxParseError, elevation_profile, parse_gpx

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
