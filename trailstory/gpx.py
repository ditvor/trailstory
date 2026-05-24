from __future__ import annotations

from itertools import pairwise
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import gpxpy

from trailstory.models import GpxStats, Waypoint


class GpxParseError(Exception):
    """Raised when the GPX file cannot be parsed or contains no track points."""


def parse_gpx(path: Path) -> GpxStats:
    with open(path, encoding="utf-8") as fh:
        try:
            gpx = gpxpy.parse(fh)
        except Exception as exc:
            raise GpxParseError(f"Failed to parse GPX at {path}: {exc}") from exc

    return _stats_from_parsed(gpx, path)


def extract_track_name(path: Path) -> str | None:
    """Return the human-readable name from a GPX file, or ``None``.

    Tries the file-level ``<name>`` first (most exporters set this),
    then the first track's name. Strips and clips to 120 chars so a
    pathological file cannot blow up the chip UI. Returns ``None`` on
    parse failure or when no name is present — the caller decides what
    to substitute (blank chip, prompt the user, etc.).

    Used by the builder's GPX preview endpoint to populate the
    AUTO-EXTRACTED location chip with "from track" provenance.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            gpx = gpxpy.parse(fh)
    except Exception:
        return None
    candidates: list[str | None] = [gpx.name]
    for track in gpx.tracks:
        candidates.append(track.name)
    for candidate in candidates:
        if candidate:
            cleaned = candidate.strip()[:120]
            if cleaned:
                return cleaned
    return None


def _stats_from_parsed(gpx: gpxpy.gpx.GPX, path: Path) -> GpxStats:
    waypoints: list[Waypoint] = []
    for track in gpx.tracks:
        for segment in track.segments:
            for pt in segment.points:
                waypoints.append(
                    Waypoint(
                        lat=pt.latitude,
                        lon=pt.longitude,
                        ele_m=float(pt.elevation) if pt.elevation is not None else 0.0,
                        time=pt.time,
                    )
                )

    if not waypoints:
        raise GpxParseError(f"GPX at {path} contains no track points")

    moving = gpx.get_moving_data()
    uphill_downhill = gpx.get_uphill_downhill()
    extremes = gpx.get_elevation_extremes()

    distance_m = moving.moving_distance if moving else gpx.length_2d()
    duration_s = int(moving.moving_time) if moving else 0
    gain_m = float(uphill_downhill.uphill) if uphill_downhill else 0.0
    min_elev = (
        float(extremes.minimum) if extremes and extremes.minimum is not None else waypoints[0].ele_m
    )
    max_elev = (
        float(extremes.maximum) if extremes and extremes.maximum is not None else waypoints[0].ele_m
    )

    return GpxStats(
        distance_km=round(distance_m / 1000.0, 3),
        elevation_gain_m=round(gain_m, 1),
        duration_min=duration_s // 60,
        start_elev_m=round(min_elev, 1),
        summit_elev_m=round(max_elev, 1),
        waypoints=waypoints,
    )


def elevation_profile(stats: GpxStats, n: int = 20) -> list[tuple[float, float]]:
    """Return n normalised (x, y) points for an SVG elevation profile.

    x ∈ [0, 1] along cumulative 2D distance; y ∈ [0, 1] where 0 = lowest
    elevation and 1 = highest. If all elevations are equal, y = 0.5.
    The caller is responsible for SVG y-inversion.
    """
    if n < 2:
        raise ValueError("n must be >= 2")
    pts = stats.waypoints
    if len(pts) < 2:
        return [(0.0, 0.5), (1.0, 0.5)]

    cum: list[float] = [0.0]
    for a, b in pairwise(pts):
        cum.append(cum[-1] + _haversine_m(a.lat, a.lon, b.lat, b.lon))
    total = cum[-1]
    if total == 0:
        return [(i / (n - 1), 0.5) for i in range(n)]

    elevs = [p.ele_m for p in pts]
    lo, hi = min(elevs), max(elevs)
    span = hi - lo

    out: list[tuple[float, float]] = []
    for i in range(n):
        x = i / (n - 1)
        ele = _interp(cum, elevs, x * total)
        y = 0.5 if span == 0 else (ele - lo) / span
        out.append((round(x, 6), round(y, 6)))
    return out


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    t = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + t * (ys[hi] - ys[lo])


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))
