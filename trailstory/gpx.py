from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from statistics import median

import gpxpy

from trailstory.models import GpxStats, Pause, PhotoMeta, PhotoPosition, TrackShape, Waypoint

# ADR-015 — heuristic constants for pause detection and track-shape
# classification. All tuned against typical phone-recorded tracks
# (sub-second to ~30s point intervals); manually-edited GPX files with
# extremely sparse waypoints simply don't surface pauses, which is the
# right fail-quiet behaviour.

# Below this instantaneous velocity (m/s) the walker is treated as
# stationary. 0.3 m/s ≈ 1 km/h — slower than even a slow stroll, fast
# enough to absorb GPS jitter when standing still.
_PAUSE_VELOCITY_THRESHOLD_MPS: float = 0.3
# Pause clusters shorter than this in seconds are dropped as noise.
# 5 minutes is the shortest pause that usually means something for
# the narrative (a snack, a view, a nappy change).
_PAUSE_MIN_DURATION_SEC: int = 5 * 60
# Endpoints (first ↔ last waypoint) within this many metres are treated
# as "returned to start" — the precondition for loop / out-and-back.
# 200m is generous enough to absorb GPS noise on a parking-lot start.
_ENDPOINT_PROXIMITY_M: float = 200.0
# Loop vs out-and-back: compare the track's bounding-box max dimension
# to half the total distance. A perfect out-and-back has ratio ≈ 1 (one
# way out, one way back along the same line); a circular loop has
# ratio ≈ 2/π ≈ 0.64. The threshold is chosen high enough that a
# zig-zagging out-and-back trail still classifies as out-and-back, and
# low enough that an oval-ish loop doesn't get mistaken for one.
_OUT_AND_BACK_BBOX_RATIO: float = 0.8
# Timestamp-fallback photo matching: omit a photo whose best time delta
# to any waypoint exceeds this, instead of confidently placing it at a
# wrong km. EXIF DateTimeOriginal is naive local wall-clock while GPX
# times are UTC, so an uncalibrated comparison is off by the timezone
# offset (1-2h for the core Munich user) — far beyond this bound. When
# the same upload contains GPS-bearing photos, their matches calibrate
# the clock offset first (see correlate_photos_to_track), which brings
# same-session photos back inside the bound.
_TIMESTAMP_MATCH_MAX_DELTA_SEC: float = 30 * 60


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
    loss_m = float(uphill_downhill.downhill) if uphill_downhill else 0.0
    min_elev = (
        float(extremes.minimum) if extremes and extremes.minimum is not None else waypoints[0].ele_m
    )
    max_elev = (
        float(extremes.maximum) if extremes and extremes.maximum is not None else waypoints[0].ele_m
    )

    # ADR-015 — read the GPX track name (file-level <name> first, then the
    # first track's name) so the ledger has a place-name to fall back on
    # when the user didn't supply ``location_name``. Same source as
    # ``extract_track_name``; kept inline here to avoid re-parsing.
    name = gpx.name
    if not name:
        for track in gpx.tracks:
            if track.name:
                name = track.name
                break
    track_name = name.strip()[:120] if name and name.strip() else None

    track_shape = _classify_track_shape(waypoints)
    pauses = _detect_pauses(waypoints)
    distance_km = round(distance_m / 1000.0, 3)
    # at_km comes from cumulative point-to-point distance, which includes
    # stationary GPS jitter that the moving-distance basis of distance_km
    # excludes — on pause-heavy tracks the cumulative basis can overshoot
    # by a few percent. Clamp so the ledger never reports a pause beyond
    # the hike's own length.
    pauses = [
        p.model_copy(update={"at_km": distance_km}) if p.at_km > distance_km else p for p in pauses
    ]

    return GpxStats(
        distance_km=distance_km,
        elevation_gain_m=round(gain_m, 1),
        elevation_loss_m=round(loss_m, 1),
        duration_min=duration_s // 60,
        start_elev_m=round(min_elev, 1),
        summit_elev_m=round(max_elev, 1),
        track_name=track_name,
        track_shape=track_shape,
        pauses=pauses,
        waypoints=waypoints,
    )


# ── ADR-015 — pause detection and track-shape classification ─────────────────


def _detect_pauses(waypoints: list[Waypoint]) -> list[Pause]:
    """Return the pauses (≥ 5 min) detected in the timed waypoint stream.

    Walks adjacent waypoint pairs, computing instantaneous velocity from
    the haversine distance and the timestamp delta. Consecutive segments
    whose velocity falls below :data:`_PAUSE_VELOCITY_THRESHOLD_MPS` are
    clustered; clusters whose total duration is below
    :data:`_PAUSE_MIN_DURATION_SEC` are dropped as noise. Segments
    without timestamps (manually-edited GPX, or a sparse export) break
    any open cluster and are themselves skipped — we cannot place a
    pause we cannot measure.

    The pause's ``lat`` / ``lon`` / ``ele_m`` are sampled from the
    cluster's midpoint waypoint, which is a stable representative
    location when the walker drifted around a small area. ``at_km`` is
    the cumulative track distance at the START of the cluster, so the
    writer can ground "around the 3.5 km mark" rather than the centroid.
    """
    if len(waypoints) < 2:
        return []

    # Pre-compute cumulative distance so each pause carries an at_km
    # without walking the track twice.
    cum_m: list[float] = [0.0]
    for a, b in pairwise(waypoints):
        cum_m.append(cum_m[-1] + _haversine_m(a.lat, a.lon, b.lat, b.lon))

    pauses: list[Pause] = []
    cluster_start: int | None = None

    for i in range(len(waypoints) - 1):
        a, b = waypoints[i], waypoints[i + 1]
        if a.time is None or b.time is None:
            # An untimed segment cannot be classified; flush any open
            # cluster ending at the previous waypoint and skip.
            if cluster_start is not None:
                _maybe_emit_pause(pauses, waypoints, cum_m, cluster_start, i)
                cluster_start = None
            continue

        dt_sec = (b.time - a.time).total_seconds()
        if dt_sec <= 0:
            # Non-monotonic timestamps (some watches do this on re-sync);
            # treat as untimed and reset.
            if cluster_start is not None:
                _maybe_emit_pause(pauses, waypoints, cum_m, cluster_start, i)
                cluster_start = None
            continue

        velocity = (cum_m[i + 1] - cum_m[i]) / dt_sec

        if velocity < _PAUSE_VELOCITY_THRESHOLD_MPS:
            if cluster_start is None:
                cluster_start = i
        else:
            if cluster_start is not None:
                _maybe_emit_pause(pauses, waypoints, cum_m, cluster_start, i)
                cluster_start = None

    # Trailing cluster — pause that ran to the end of the track.
    if cluster_start is not None:
        _maybe_emit_pause(pauses, waypoints, cum_m, cluster_start, len(waypoints) - 1)

    return pauses


def _maybe_emit_pause(
    pauses: list[Pause],
    waypoints: list[Waypoint],
    cum_m: list[float],
    start_idx: int,
    end_idx: int,
) -> None:
    """Append a Pause if the cluster meets the minimum duration."""
    start = waypoints[start_idx]
    end = waypoints[end_idx]
    if start.time is None or end.time is None:
        return
    duration_sec = (end.time - start.time).total_seconds()
    if duration_sec < _PAUSE_MIN_DURATION_SEC:
        return
    mid_idx = (start_idx + end_idx) // 2
    mid = waypoints[mid_idx]
    pauses.append(
        Pause(
            at_km=round(cum_m[start_idx] / 1000.0, 3),
            duration_min=int(duration_sec // 60),
            lat=mid.lat,
            lon=mid.lon,
            ele_m=mid.ele_m,
        )
    )


def correlate_photos_to_track(photos: list[PhotoMeta], gpx_stats: GpxStats) -> list[PhotoPosition]:
    """Match each photo to a position along the GPX track (ADR-015).

    Two-strategy matching, preferred order:

    1. **GPS proximity.** When the photo carries EXIF GPS coordinates
       (read by :func:`trailstory.photos.load_photos` before the
       strip-on-save step), find the waypoint with the smallest
       great-circle distance to the photo's coordinates. Most reliable —
       independent of any clock-sync or timezone confusion between the
       camera and the GPS logger.
    2. **Timestamp matching with clock calibration.** When the photo
       has no GPS, find the timed waypoint with the smallest absolute
       time delta. EXIF ``DateTimeOriginal`` is naive **local
       wall-clock** while GPX timestamps are UTC, so a raw comparison
       is systematically off by the timezone offset. To correct for
       this, the GPS-matched photos from strategy 1 double as clock
       anchors: the median of (matched-waypoint time - photo EXIF
       time) across them is applied as an offset to every GPS-less
       photo before matching. Without any GPS anchors no calibration
       is possible, and the raw comparison usually exceeds the
       :data:`_TIMESTAMP_MATCH_MAX_DELTA_SEC` guard below.

    A photo whose best match is worse than
    :data:`_TIMESTAMP_MATCH_MAX_DELTA_SEC` — or that has no GPS and no
    timed waypoints to compare against — is omitted from the output
    rather than included with a guessed position. Better to leave the
    writer ungrounded for that beat than to feed it a wrong km marker.

    ``km_along_track`` is clamped to ``gpx_stats.distance_km``:
    cumulative point-to-point distance includes stationary GPS jitter
    that gpxpy's moving distance excludes, so the cumulative basis can
    overshoot the headline distance by a few percent on pause-heavy
    tracks.

    Returns positions in input-photo order, indexed by ``photo_index``.
    """
    if not photos or not gpx_stats.waypoints:
        return []

    waypoints = gpx_stats.waypoints
    cum_m: list[float] = [0.0]
    for a, b in pairwise(waypoints):
        cum_m.append(cum_m[-1] + _haversine_m(a.lat, a.lon, b.lat, b.lon))

    # Pass 1: GPS matches. Each one also yields a clock-offset sample
    # (waypoint UTC wall-clock - photo EXIF wall-clock) used to
    # calibrate the GPS-less photos in pass 2.
    matched: dict[int, int] = {}  # photo.index -> waypoint index
    offset_samples: list[float] = []
    for photo in photos:
        if photo.gps_lat is None or photo.gps_lon is None:
            continue
        idx = _nearest_waypoint_by_gps(photo.gps_lat, photo.gps_lon, waypoints)
        matched[photo.index] = idx
        wp_time = waypoints[idx].time
        if wp_time is not None:
            offset_samples.append((_naive(wp_time) - _naive(photo.timestamp)).total_seconds())

    clock_offset_sec = median(offset_samples) if offset_samples else 0.0

    # Pass 2: timestamp matches for the GPS-less photos, calibrated by
    # the pass-1 offset, with the max-delta guard.
    timed: list[tuple[int, datetime]] = []
    for i, wp in enumerate(waypoints):
        if wp.time is not None:
            timed.append((i, _naive(wp.time)))
    for photo in photos:
        if photo.index in matched or not timed:
            continue
        target = _naive(photo.timestamp) + timedelta(seconds=clock_offset_sec)
        best_idx, best_naive = timed[0]
        best_dt = abs((target - best_naive).total_seconds())
        for i, wp_naive in timed[1:]:
            dt = abs((target - wp_naive).total_seconds())
            if dt < best_dt:
                best_dt = dt
                best_idx = i
        if best_dt <= _TIMESTAMP_MATCH_MAX_DELTA_SEC:
            matched[photo.index] = best_idx

    positions: list[PhotoPosition] = []
    for photo in photos:
        match_idx = matched.get(photo.index)
        if match_idx is None:
            continue
        wp = waypoints[match_idx]
        positions.append(
            PhotoPosition(
                photo_index=photo.index,
                km_along_track=min(round(cum_m[match_idx] / 1000.0, 3), gpx_stats.distance_km),
                ele_m=round(wp.ele_m, 1),
            )
        )
    return positions


def _naive(dt: datetime) -> datetime:
    """Strip tzinfo for wall-clock comparison (EXIF carries no tz tag)."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _nearest_waypoint_by_gps(lat: float, lon: float, waypoints: list[Waypoint]) -> int:
    """Index of the waypoint with the smallest great-circle distance."""
    best_idx = 0
    best_dist = _haversine_m(lat, lon, waypoints[0].lat, waypoints[0].lon)
    for i in range(1, len(waypoints)):
        d = _haversine_m(lat, lon, waypoints[i].lat, waypoints[i].lon)
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def _classify_track_shape(waypoints: list[Waypoint]) -> TrackShape:
    """Classify the track topology (ADR-015).

    Two-step decision:

    1. If the first and last waypoints are more than
       :data:`_ENDPOINT_PROXIMITY_M` apart, the hike ends somewhere
       other than where it started → ``point_to_point``.
    2. Otherwise the walker returned to (approximately) the start.
       Compute the track's bounding box and compare its largest
       dimension against half the total track distance. An out-and-back
       hugs a straight-ish line whose length equals the one-way
       distance ≈ half the total, so the ratio is near 1. A loop
       spreads across two dimensions, so the ratio is lower (≈ 2/π for
       a perfect circle). The threshold (:data:`_OUT_AND_BACK_BBOX_RATIO`)
       sits between the two.

    Degenerate input (zero or one waypoint, no measurable distance)
    falls through to ``point_to_point`` — the writer treats this as the
    safest, least-committal framing.
    """
    if len(waypoints) < 2:
        return TrackShape.point_to_point
    start = waypoints[0]
    end = waypoints[-1]
    endpoint_distance = _haversine_m(start.lat, start.lon, end.lat, end.lon)
    if endpoint_distance > _ENDPOINT_PROXIMITY_M:
        return TrackShape.point_to_point

    # Endpoints meet. Bounding-box vs total-distance ratio decides
    # loop vs out-and-back.
    lats = [w.lat for w in waypoints]
    lons = [w.lon for w in waypoints]
    mid_lat = sum(lats) / len(lats)
    # Compute lat / lon spans in metres at the track's mean latitude,
    # so longitude scaling is correct (cos(47°) ≈ 0.68x equator).
    lat_span_m = _haversine_m(min(lats), mid_lat, max(lats), mid_lat)
    lon_span_m = _haversine_m(mid_lat, min(lons), mid_lat, max(lons))
    max_span_m = max(lat_span_m, lon_span_m)
    total_m = sum(_haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in pairwise(waypoints))
    if total_m < 1.0:
        # Degenerate track, no real motion. Endpoints meet by accident.
        return TrackShape.point_to_point
    ratio = max_span_m / (total_m / 2.0)
    if ratio > _OUT_AND_BACK_BBOX_RATIO:
        return TrackShape.out_and_back
    return TrackShape.loop


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
