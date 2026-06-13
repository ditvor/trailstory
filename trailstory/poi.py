"""Deterministic POI name resolution (ADR-019).

Turn a hiker's generic landmark beat ("the wax-figure church") into a real
named OpenStreetMap feature ("Mühlfeldkirche") — when, and only when, the
match is unambiguous. The name is a sourced fact (OSM, ODbL), never an LLM
guess; it flows into the place stitch as additional grounded input.

Pipeline:

1. :func:`fetch_pois_near_track` queries the Overpass API for named features
   of a fixed set of "landmark" categories within a tight radius of the
   track, bounded by the track's (padded) bounding box.
2. :func:`categorize_beat` maps a hiker beat to one of those categories by
   keyword (English + German).
3. :func:`match_beats_to_pois` matches a beat to a feature **only if exactly
   one** named feature of the beat's category is near the track. Zero or
   several candidates → skip. This single-candidate rule is the entire
   safety guarantee: a wrong name is worse than a generic one (ADR-019).

Everything soft-fails to "no matches": no network, a timeout, a malformed
response, or any ambiguity all yield an empty list, and the place block falls
back to the ADR-017 generic behaviour. All network goes through the single
:func:`_overpass_get` seam so tests stay offline. No new dependency — the one
POST-with-JSON call uses stdlib ``urllib``.
"""

from __future__ import annotations

import json
import logging
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Final

from trailstory.models import PoiMatch, Waypoint

logger = logging.getLogger(__name__)

_OVERPASS_URL: Final[str] = "https://overpass-api.de/api/interpreter"
_USER_AGENT: Final[str] = "trailstory/0.1 (poi-resolution; +https://github.com/trailstory)"
_HTTP_TIMEOUT_SECONDS: Final[float] = 25.0

# How close (metres) a feature must be to any track waypoint to count as
# "passed". Tight on purpose — a church 500 m off-route is not the one the
# hiker means.
_DEFAULT_RADIUS_M: Final[float] = 200.0
# Cap on the bounding box span we will query. A bbox larger than this means a
# point-to-point hike spanning a whole region; querying every church in a
# Bavarian district is both slow and useless for naming a single landmark.
_MAX_BBOX_SPAN_DEG: Final[float] = 0.5


@dataclass(frozen=True)
class _Category:
    """One landmark category: hiker keywords + the OSM tags that mark it."""

    label: str
    # Hiker-beat keywords (English + German), matched whole-word, lowercase.
    keywords: tuple[str, ...]
    # OSM (key, allowed-values) pairs. Empty values = "any value for this key".
    tags: tuple[tuple[str, tuple[str, ...]], ...]


# Categories a hiker would actually name. Kept deliberately narrow — generic
# words ("mountain", "bridge", "river") are excluded because they are both too
# common in prose and too common on the map to resolve to one feature.
_CATEGORIES: Final[tuple[_Category, ...]] = (
    _Category(
        "church",
        ("church", "chapel", "kirche", "kapelle", "cathedral", "dom", "münster", "basilica"),
        (("amenity", ("place_of_worship",)), ("building", ("church", "chapel", "cathedral"))),
    ),
    _Category(
        "monastery",
        ("monastery", "abbey", "kloster", "convent", "priory"),
        (("amenity", ("monastery",)), ("historic", ("monastery",)), ("building", ("monastery",))),
    ),
    _Category(
        "peak",
        ("peak", "summit", "gipfel", "spitze", "kogel"),
        (("natural", ("peak",)),),
    ),
    _Category(
        "lake",
        ("lake", "pond", "tarn", "weiher", "reservoir"),
        (("water", ("lake", "pond", "reservoir")),),
    ),
    _Category(
        "hut",
        ("hut", "hütte", "almhütte", "berghütte", "refuge"),
        (("tourism", ("alpine_hut", "wilderness_hut")),),
    ),
    _Category(
        "castle",
        ("castle", "schloss", "burg", "fortress", "ruine", "fort"),
        (("historic", ("castle", "ruins", "fort")), ("ruins", ("castle",))),
    ),
    _Category(
        "waterfall",
        ("waterfall", "wasserfall", "cascade"),
        (("waterfall", ()), ("natural", ("waterfall",))),
    ),
    _Category(
        "viewpoint",
        ("viewpoint", "lookout", "aussichtspunkt", "vista", "overlook"),
        (("tourism", ("viewpoint",)),),
    ),
)


@dataclass(frozen=True)
class Poi:
    """A named OSM feature fetched near the track."""

    name: str
    category: str
    lat: float
    lon: float


def resolve_poi_matches(
    waypoints: list[Waypoint],
    beats: list[str],
    *,
    radius_m: float = _DEFAULT_RADIUS_M,
) -> list[PoiMatch]:
    """Fetch POIs near the track and match the hiker's beats to them.

    The top-level entry point. Soft-fails to ``[]`` at every step — no
    waypoints, no recognised beat category, a failed Overpass call, or an
    ambiguous match all yield no matches. Never raises.
    """
    if not waypoints or not beats:
        return []
    # Only bother fetching when at least one beat looks like a nameable POI.
    if not any(categorize_beat(b) is not None for b in beats):
        return []
    pois = fetch_pois_near_track(waypoints, radius_m=radius_m)
    if not pois:
        return []
    return match_beats_to_pois(beats, pois)


def categorize_beat(beat: str) -> str | None:
    """Return the landmark category a beat names, or ``None``.

    Whole-word, case-insensitive keyword match. ``None`` means the beat is
    not a recognised POI type (most beats — "brunch", "river walk").
    """
    lowered = beat.lower()
    for category in _CATEGORIES:
        for kw in category.keywords:
            if re.search(rf"\b{re.escape(kw)}\b", lowered):
                return category.label
    return None


def fetch_pois_near_track(
    waypoints: list[Waypoint],
    *,
    radius_m: float = _DEFAULT_RADIUS_M,
) -> list[Poi]:
    """Query Overpass for named landmark features near the track.

    Returns named features (those with an OSM ``name`` tag and a recognised
    category) within ``radius_m`` of any waypoint. ``[]`` on any failure or
    when the track's bounding box is implausibly large.
    """
    bbox = _padded_bbox(waypoints, radius_m)
    if bbox is None:
        return []
    elements = _overpass_get(_build_query(bbox))
    if not elements:
        return []

    out: list[Poi] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        tags = el.get("tags")
        if not isinstance(tags, dict):
            continue
        name = tags.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        category = _category_of_tags(tags)
        if category is None:
            continue
        coord = _element_coord(el)
        if coord is None:
            continue
        lat, lon = coord
        if _min_distance_to_track_m(lat, lon, waypoints) <= radius_m:
            out.append(Poi(name=name.strip(), category=category, lat=lat, lon=lon))
    return out


def match_beats_to_pois(beats: list[str], pois: list[Poi]) -> list[PoiMatch]:
    """Match each beat to a single POI of its category, or skip it.

    Conservative: a beat matches only if **exactly one** fetched POI shares
    its category. Zero → nothing to name; two or more → ambiguous → skip
    (ADR-019). A given POI name is used at most once across beats.
    """
    matches: list[PoiMatch] = []
    used_names: set[str] = set()
    seen_beats: set[str] = set()
    for beat in beats:
        if beat in seen_beats:
            continue
        seen_beats.add(beat)
        category = categorize_beat(beat)
        if category is None:
            continue
        candidates = [p for p in pois if p.category == category and p.name not in used_names]
        if len(candidates) != 1:
            if len(candidates) > 1:
                logger.info(
                    "poi: %d candidates for beat %r (category %s); ambiguous, skipping",
                    len(candidates),
                    beat,
                    category,
                )
            continue
        poi = candidates[0]
        used_names.add(poi.name)
        matches.append(PoiMatch(beat=beat, name=poi.name, category=category))
    return matches


# -- internal helpers ---------------------------------------------------------


def _build_query(bbox: tuple[float, float, float, float]) -> str:
    """Build an Overpass QL query for every category's tags within ``bbox``."""
    south, west, north, east = bbox
    box = f"{south:.5f},{west:.5f},{north:.5f},{east:.5f}"
    lines = [f"  nwr{flt}({box});" for category in _CATEGORIES for flt in _filter_strings(category)]
    body = "\n".join(lines)
    return f"[out:json][timeout:25];\n(\n{body}\n);\nout center tags;"


def _filter_strings(category: _Category) -> list[str]:
    out: list[str] = []
    for key, values in category.tags:
        if values:
            out.extend(f'["{key}"="{v}"]' for v in values)
        else:
            out.append(f'["{key}"]')
    return out


def _category_of_tags(tags: dict[str, Any]) -> str | None:
    """Map an OSM element's tags back to one of our category labels."""
    for category in _CATEGORIES:
        for key, values in category.tags:
            value = tags.get(key)
            if not isinstance(value, str):
                continue
            if values and value not in values:
                continue
            # ``amenity=place_of_worship`` is multi-faith — a mosque or
            # synagogue must not be labelled "church". Only a Christian one
            # (or a ``building=church`` tag, handled by the other entry)
            # counts. Validated live: a Bad Tölz mosque was tagged only
            # ``place_of_worship``.
            if category.label == "church" and key == "amenity":
                if tags.get("religion") != "christian":
                    continue
            return category.label
    return None


def _element_coord(element: dict[str, Any]) -> tuple[float, float] | None:
    """Pull a (lat, lon) from a node (lat/lon) or way/relation (center)."""
    lat = element.get("lat")
    lon = element.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return (float(lat), float(lon))
    center = element.get("center")
    if isinstance(center, dict):
        clat = center.get("lat")
        clon = center.get("lon")
        if isinstance(clat, (int, float)) and isinstance(clon, (int, float)):
            return (float(clat), float(clon))
    return None


def _padded_bbox(
    waypoints: list[Waypoint], radius_m: float
) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) around the track, padded by ``radius_m``.

    ``None`` when there are no waypoints or the span is implausibly large
    (a region-spanning track is not about a single landmark).
    """
    if not waypoints:
        return None
    lats = [w.lat for w in waypoints]
    lons = [w.lon for w in waypoints]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    if (max_lat - min_lat) > _MAX_BBOX_SPAN_DEG or (max_lon - min_lon) > _MAX_BBOX_SPAN_DEG:
        logger.info("poi: track bbox too large to query; skipping")
        return None
    pad_lat = radius_m / 111_000.0
    mid_lat = (min_lat + max_lat) / 2.0
    pad_lon = radius_m / (111_000.0 * max(0.1, math.cos(math.radians(mid_lat))))
    return (min_lat - pad_lat, min_lon - pad_lon, max_lat + pad_lat, max_lon + pad_lon)


def _min_distance_to_track_m(lat: float, lon: float, waypoints: list[Waypoint]) -> float:
    """Smallest great-circle distance (m) from (lat, lon) to any waypoint."""
    return min(_haversine_m(lat, lon, w.lat, w.lon) for w in waypoints)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _overpass_get(query: str) -> list[Any] | None:
    """POST ``query`` to Overpass and return the ``elements`` list, or ``None``.

    The single network seam — tests monkeypatch this. Any failure (DNS,
    timeout, non-2xx, non-JSON, missing elements) is swallowed and logged:
    POI resolution is additive and must never raise into a render.
    """
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(_OVERPASS_URL, data=data, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("overpass request failed: %s", exc)
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("overpass returned non-JSON: %s", exc)
        return None
    elements = parsed.get("elements") if isinstance(parsed, dict) else None
    return elements if isinstance(elements, list) else None
