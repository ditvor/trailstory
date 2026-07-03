"""Tests for ADR-019 POI name resolution.

All offline: the single network seam ``trailstory.poi._overpass_get`` is
monkeypatched, so no test touches the Overpass API. Covers beat
categorisation, the conservative single-candidate matcher, Overpass element
parsing + distance filtering, and the soft-fail paths.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import trailstory.poi as poi_mod
from trailstory.models import PoiMatch, Waypoint
from trailstory.poi import (
    Poi,
    categorize_beat,
    fetch_pois_near_track,
    match_beats_to_pois,
    resolve_poi_matches,
)

# A short track around Bad Tölz (47.76, 11.56).
_TRACK = [
    Waypoint(lat=47.760, lon=11.556, ele_m=660.0),
    Waypoint(lat=47.762, lon=11.558, ele_m=665.0),
    Waypoint(lat=47.764, lon=11.560, ele_m=670.0),
]


# ── categorize_beat ──────────────────────────────────────────────────────────


def test_categorize_beat_recognises_landmarks():
    assert categorize_beat("the wax-figure church") == "church"
    assert categorize_beat("a creepy old church") == "church"
    assert categorize_beat("the quiet lake") == "lake"
    assert categorize_beat("the summit") == "peak"
    assert categorize_beat("an old castle") == "castle"


def test_categorize_beat_returns_none_for_non_poi():
    assert categorize_beat("brunch") is None
    assert categorize_beat("river walk") is None
    assert categorize_beat("a picnic blanket") is None


def test_categorize_beat_is_whole_word():
    # "see" must not match inside "Tegernsee" or "oversee".
    assert categorize_beat("we could oversee the valley") is None


# ── match_beats_to_pois ──────────────────────────────────────────────────────


def _church(name: str) -> Poi:
    return Poi(name=name, category="church", lat=47.76, lon=11.556)


def test_match_single_candidate():
    matches = match_beats_to_pois(["the wax-figure church"], [_church("Mühlfeldkirche")])
    assert matches == [
        PoiMatch(beat="the wax-figure church", name="Mühlfeldkirche", category="church")
    ]


def test_match_skips_when_ambiguous():
    pois = [_church("Mühlfeldkirche"), _church("Stadtpfarrkirche")]
    assert match_beats_to_pois(["the church"], pois) == []


def test_match_skips_when_no_candidate():
    lake = Poi(name="Kirchsee", category="lake", lat=47.76, lon=11.556)
    assert match_beats_to_pois(["the church"], [lake]) == []


def test_match_skips_non_poi_beats():
    assert match_beats_to_pois(["brunch", "river walk"], [_church("Mühlfeldkirche")]) == []


def test_match_uses_each_name_once():
    # Two church beats, one church POI → only the first beat is named.
    matches = match_beats_to_pois(
        ["the wax-figure church", "the other church"], [_church("Mühlfeldkirche")]
    )
    assert len(matches) == 1
    assert matches[0].beat == "the wax-figure church"


# ── fetch_pois_near_track ────────────────────────────────────────────────────


def _fake_overpass(elements: list[Any]) -> Callable[[str], list[Any] | None]:
    return lambda _query: elements


def test_fetch_parses_nodes_and_way_centers(monkeypatch):
    elements = [
        {
            "type": "node",
            "lat": 47.760,
            "lon": 11.556,
            "tags": {
                "amenity": "place_of_worship",
                "religion": "christian",
                "name": "Mühlfeldkirche",
            },
        },
        {
            "type": "way",
            "center": {"lat": 47.762, "lon": 11.558},
            "tags": {"water": "lake", "name": "Kirchsee"},
        },
    ]
    monkeypatch.setattr(poi_mod, "_overpass_get", _fake_overpass(elements))
    pois = fetch_pois_near_track(_TRACK)
    by_name = {p.name: p.category for p in pois}
    assert by_name == {"Mühlfeldkirche": "church", "Kirchsee": "lake"}


def test_fetch_skips_unnamed_and_uncategorised(monkeypatch):
    elements = [
        {
            "type": "node",
            "lat": 47.760,
            "lon": 11.556,
            "tags": {"amenity": "place_of_worship"},
        },  # no name
        {
            "type": "node",
            "lat": 47.760,
            "lon": 11.556,
            "tags": {"name": "Some Bench", "amenity": "bench"},
        },  # not a landmark
    ]
    monkeypatch.setattr(poi_mod, "_overpass_get", _fake_overpass(elements))
    assert fetch_pois_near_track(_TRACK) == []


def test_fetch_distinguishes_mosque_from_church(monkeypatch):
    # amenity=place_of_worship is multi-faith: only a Christian one (or a
    # building=church) is a "church"; a mosque is dropped, not mislabelled.
    elements = [
        {
            "type": "node",
            "lat": 47.760,
            "lon": 11.556,
            "tags": {
                "amenity": "place_of_worship",
                "religion": "muslim",
                "name": "Mavlana Moschee",
            },
        },
        {
            "type": "node",
            "lat": 47.760,
            "lon": 11.556,
            "tags": {
                "amenity": "place_of_worship",
                "religion": "christian",
                "name": "Stadtpfarrkirche",
            },
        },
        {
            "type": "node",
            "lat": 47.760,
            "lon": 11.556,
            "tags": {"building": "church", "name": "Alte Kirche"},
        },
    ]
    monkeypatch.setattr(poi_mod, "_overpass_get", _fake_overpass(elements))
    by_name = {p.name: p.category for p in fetch_pois_near_track(_TRACK)}
    assert by_name == {"Stadtpfarrkirche": "church", "Alte Kirche": "church"}
    assert "Mavlana Moschee" not in by_name


def test_fetch_filters_far_features(monkeypatch):
    elements = [
        {
            "type": "node",
            "lat": 48.50,
            "lon": 11.556,
            "tags": {"amenity": "place_of_worship", "religion": "christian", "name": "Far Church"},
        },
    ]
    monkeypatch.setattr(poi_mod, "_overpass_get", _fake_overpass(elements))
    assert fetch_pois_near_track(_TRACK) == []


def test_fetch_soft_fails_on_overpass_none(monkeypatch):
    monkeypatch.setattr(poi_mod, "_overpass_get", lambda _q: None)
    assert fetch_pois_near_track(_TRACK) == []


# ── resolve_poi_matches (end to end) ─────────────────────────────────────────


def test_resolve_end_to_end(monkeypatch):
    elements = [
        {
            "type": "node",
            "lat": 47.760,
            "lon": 11.556,
            "tags": {
                "amenity": "place_of_worship",
                "religion": "christian",
                "name": "Mühlfeldkirche",
            },
        },
    ]
    monkeypatch.setattr(poi_mod, "_overpass_get", _fake_overpass(elements))
    matches = resolve_poi_matches(_TRACK, ["river walk", "the wax-figure church", "brunch"])
    assert matches == [
        PoiMatch(beat="the wax-figure church", name="Mühlfeldkirche", category="church")
    ]


def test_resolve_skips_overpass_when_no_poi_beats(monkeypatch):
    called = False

    def _spy(_query: str) -> list[Any] | None:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(poi_mod, "_overpass_get", _spy)
    assert resolve_poi_matches(_TRACK, ["brunch", "river walk"]) == []
    assert called is False  # no nameable beat → no network call


def test_resolve_empty_for_no_waypoints():
    assert resolve_poi_matches([], ["the church"]) == []
