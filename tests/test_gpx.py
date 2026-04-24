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
