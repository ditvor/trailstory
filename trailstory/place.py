"""Deterministic place resolution: GPS coordinates → town + reference extract.

ADR-017. The "about this place" block needs two grounded inputs; this module
produces the *external* one — a town name and a factual reference extract —
entirely from public data keyed on the hike's coordinates, never from an
LLM's memory:

* reverse geocoding (OpenStreetMap Nominatim) turns the track's midpoint
  coordinates into an administrative place name (town / village / city) and a
  coarse region;
* the Wikipedia REST summary endpoint turns that place name into a short
  factual extract with a citable source URL.

Everything here soft-fails to ``None``: no network, a 404, a malformed
response, or a place with no Wikipedia article all yield ``None`` (or a
town-only :class:`PlaceReference` with an empty extract), and the caller
falls back to a town-only note or omits the block entirely. Coordinates
leave the machine only when the caller has opted in (``--place`` /
``Settings.use_place_context``); that privacy trade-off is recorded in
ADR-017.

No new runtime dependency: the two GET-with-JSON calls use ``urllib`` from
the stdlib. All network goes through the single :func:`_http_get_json` seam
so tests can monkeypatch it without touching the network.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from trailstory.models import Waypoint

logger = logging.getLogger(__name__)

_NOMINATIM_REVERSE_URL: Final[str] = "https://nominatim.openstreetmap.org/reverse"
_WIKI_SUMMARY_URL: Final[str] = "https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
# Nominatim's usage policy requires a descriptive User-Agent identifying the
# application. Trailstory runs this at most once per hike, well under the
# 1 req/s ceiling.
_USER_AGENT: Final[str] = "trailstory/0.1 (place-context; +https://github.com/trailstory)"
_HTTP_TIMEOUT_SECONDS: Final[float] = 8.0

# Address keys Nominatim may use for "the town", most-specific first. We want
# the settlement a hiker would name, not the hamlet a single farm sits in, so
# town/city win over village when several are present.
_TOWN_KEYS: Final[tuple[str, ...]] = (
    "city",
    "town",
    "village",
    "municipality",
    "suburb",
    "hamlet",
)
# Region keys, coarsest-useful first. ``county`` is usually the most
# human-meaningful ("Bad Tölz-Wolfratshausen"); ``state`` ("Bavaria") is the
# fallback.
_REGION_KEYS: Final[tuple[str, ...]] = ("county", "state_district", "state")


class PlaceReference(BaseModel):
    """Grounded external inputs for the place-context stitch (ADR-017).

    Built deterministically from the hike's coordinates — never an LLM.
    ``extract`` is the factual source text (English Wikipedia summary, may be
    empty when no article was found); the stitch call translates it.
    ``source_url`` / ``source_title`` carry the CC BY-SA attribution the
    rendered page must show.
    """

    model_config = ConfigDict(frozen=True)

    town: str
    region: str | None = None
    extract: str = ""
    source_url: str | None = None
    source_title: str | None = None


def representative_coordinate(waypoints: list[Waypoint]) -> tuple[float, float] | None:
    """Pick a single (lat, lon) to geocode the hike by.

    The track midpoint is a reasonable "where was this hike" anchor — it
    sits inside the area walked rather than at a trailhead car park that may
    geocode to a different municipality. Returns ``None`` for an empty track.
    """
    if not waypoints:
        return None
    mid = waypoints[len(waypoints) // 2]
    return (mid.lat, mid.lon)


def resolve_place_reference(
    lat: float, lon: float, location_name: str | None = None
) -> PlaceReference | None:
    """Resolve coordinates (+ optional hiker-given name) to a reference.

    The town name is resolved by **trusting the hiker's own words first**:
    when ``location_name`` is supplied (the CLI ``--location`` / form field),
    it *is* the town — the reverse-geocode is used only to fill the coarse
    region. This matters because a track's midpoint can fall in a small
    neighbouring municipality (a Bad Tölz hike whose midpoint geocodes to
    Wackersberg across the river), and the hiker's stated place is more
    authoritative than a coordinate guess. Only when no name is given do we
    take the geocoded town.

    Then the town's Wikipedia summary supplies the factual extract. A town
    with a *failed* Wikipedia lookup still returns a town-only reference
    (empty extract) so the stitch can produce the fallback one-liner.
    ``None`` is returned only when there is no town at all — neither given
    nor geocoded.
    """
    geocoded = reverse_geocode(lat, lon)
    geo_town = geocoded[0] if geocoded else None
    region = geocoded[1] if geocoded else None

    town = (location_name or geo_town or "").strip()
    if not town:
        logger.info("no place name (given or geocoded) for (%.4f, %.4f); no place block", lat, lon)
        return None

    summary = fetch_wikipedia_summary(town)
    if summary is None:
        logger.info("no Wikipedia summary for %r; using town-only place reference", town)
        return PlaceReference(town=town, region=region)
    extract, source_url = summary
    return PlaceReference(
        town=town,
        region=region,
        extract=extract,
        source_url=source_url,
        source_title=town,
    )


def reverse_geocode(lat: float, lon: float) -> tuple[str, str | None] | None:
    """Reverse-geocode coordinates to ``(town, region)`` via Nominatim.

    ``zoom=10`` asks Nominatim for roughly the town administrative level, so
    the answer is a settlement name rather than a street address. Returns
    ``None`` when the request fails or no town-like key is present.
    """
    params = urllib.parse.urlencode(
        {
            "lat": f"{lat:.5f}",
            "lon": f"{lon:.5f}",
            "format": "jsonv2",
            "zoom": "10",
            "addressdetails": "1",
        }
    )
    data = _http_get_json(f"{_NOMINATIM_REVERSE_URL}?{params}")
    if not isinstance(data, dict):
        return None
    address = data.get("address")
    if not isinstance(address, dict):
        return None

    town = _first_present(address, _TOWN_KEYS)
    if town is None:
        return None
    region = _first_present(address, _REGION_KEYS)
    return (town, region)


def fetch_wikipedia_summary(title: str, lang: str = "en") -> tuple[str, str | None] | None:
    """Fetch ``(extract, page_url)`` for a Wikipedia article, or ``None``.

    Uses the REST ``page/summary`` endpoint, which returns a short plain-text
    ``extract`` plus canonical URLs. Returns ``None`` on a failed request, a
    missing/empty extract, or a disambiguation page (those have no single
    factual summary worth showing).
    """
    encoded_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = _WIKI_SUMMARY_URL.format(lang=lang, title=encoded_title)
    data = _http_get_json(url)
    if not isinstance(data, dict):
        return None
    if data.get("type") == "disambiguation":
        return None
    extract = data.get("extract")
    if not isinstance(extract, str) or not extract.strip():
        return None
    page_url = _content_page_url(data)
    return (extract.strip(), page_url)


# -- internal helpers ---------------------------------------------------------


def _first_present(address: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty string value among ``keys`` in ``address``."""
    for key in keys:
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _content_page_url(summary: dict[str, Any]) -> str | None:
    """Pull the canonical desktop page URL out of a REST summary payload."""
    content_urls = summary.get("content_urls")
    if isinstance(content_urls, dict):
        desktop = content_urls.get("desktop")
        if isinstance(desktop, dict):
            page = desktop.get("page")
            if isinstance(page, str) and page:
                return page
    canonical = summary.get("canonicalurl") or summary.get("canonical")
    return canonical if isinstance(canonical, str) and canonical else None


def _http_get_json(url: str) -> Any | None:
    """GET ``url`` and parse the response as JSON, or ``None`` on any failure.

    The single network seam in this module — tests monkeypatch this to avoid
    touching the network. Any error (DNS, timeout, non-2xx, non-JSON body) is
    swallowed and logged: place context is a nice-to-have and must never
    raise into a render.
    """
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("place lookup request failed for %s: %s", url, exc)
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("place lookup returned non-JSON for %s: %s", url, exc)
        return None
