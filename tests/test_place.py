"""Tests for the ADR-017 place-context feature.

Two layers, both fully offline:

* ``trailstory.place`` — deterministic geocode + Wikipedia fetch. The single
  network seam ``_http_get_json`` is monkeypatched; no test touches the
  network.
* ``trailstory.llm.place`` — the stitch call + ledger-beat extraction, with a
  ``MagicMock(spec=AnthropicClient)`` standing in for the model.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import MagicMock

import trailstory.place as place_mod
from trailstory.llm.client import AnthropicClient, LLMResponseError
from trailstory.llm.place import generate_place_context, place_beats_from_ledger
from trailstory.models import Beat, FactLedger, Person, PoiMatch, Waypoint
from trailstory.place import PlaceReference

# ── deterministic place resolution (trailstory.place) ────────────────────────

_NOMINATIM_OK: dict[str, object] = {
    "address": {
        "town": "Bad Tölz",
        "county": "Bad Tölz-Wolfratshausen",
        "state": "Bavaria",
        "country": "Germany",
    }
}
_WIKI_OK: dict[str, object] = {
    "type": "standard",
    "extract": "Bad Tölz is a town in Bavaria, Germany, on the river Isar.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Bad_Tölz"}},
}


def _fake_http(
    *, reverse: object | None = None, wiki: object | None = None
) -> Callable[[str], object | None]:
    """Build a fake ``_http_get_json`` that dispatches on the URL host."""

    def _inner(url: str) -> object | None:
        if "nominatim" in url:
            return reverse
        if "wikipedia.org" in url:
            return wiki
        return None

    return _inner


def test_reverse_geocode_parses_town_and_region(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(reverse=_NOMINATIM_OK))
    assert place_mod.reverse_geocode(47.7, 11.5) == ("Bad Tölz", "Bad Tölz-Wolfratshausen")


def test_reverse_geocode_none_when_request_fails(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(reverse=None))
    assert place_mod.reverse_geocode(47.7, 11.5) is None


def test_reverse_geocode_none_when_no_address(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(reverse={"display_name": "x"}))
    assert place_mod.reverse_geocode(47.7, 11.5) is None


def test_reverse_geocode_none_without_town_key(monkeypatch):
    monkeypatch.setattr(
        place_mod, "_http_get_json", _fake_http(reverse={"address": {"state": "Bavaria"}})
    )
    assert place_mod.reverse_geocode(47.7, 11.5) is None


def test_fetch_wikipedia_summary_parses_extract_and_url(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(wiki=_WIKI_OK))
    result = place_mod.fetch_wikipedia_summary("Bad Tölz")
    assert result is not None
    extract, url = result
    assert "Isar" in extract
    assert url == "https://en.wikipedia.org/wiki/Bad_Tölz"


def test_fetch_wikipedia_summary_uses_canonical_url_fallback(monkeypatch):
    monkeypatch.setattr(
        place_mod,
        "_http_get_json",
        _fake_http(
            wiki={"extract": "A small town.", "canonicalurl": "https://en.wikipedia.org/wiki/X"}
        ),
    )
    assert place_mod.fetch_wikipedia_summary("X") == (
        "A small town.",
        "https://en.wikipedia.org/wiki/X",
    )


def test_fetch_wikipedia_summary_skips_disambiguation(monkeypatch):
    monkeypatch.setattr(
        place_mod,
        "_http_get_json",
        _fake_http(wiki={"type": "disambiguation", "extract": "could be many places"}),
    )
    assert place_mod.fetch_wikipedia_summary("Springfield") is None


def test_fetch_wikipedia_summary_none_on_empty_extract(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(wiki={"extract": "   "}))
    assert place_mod.fetch_wikipedia_summary("Nowhere") is None


def test_resolve_place_reference_happy_path(monkeypatch):
    monkeypatch.setattr(
        place_mod, "_http_get_json", _fake_http(reverse=_NOMINATIM_OK, wiki=_WIKI_OK)
    )
    ref = place_mod.resolve_place_reference(47.7, 11.5)
    assert ref is not None
    assert ref.town == "Bad Tölz"
    assert ref.region == "Bad Tölz-Wolfratshausen"
    assert "Isar" in ref.extract
    assert ref.source_url == "https://en.wikipedia.org/wiki/Bad_Tölz"
    assert ref.source_title == "Bad Tölz"


def test_resolve_place_reference_town_only_when_no_wiki(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(reverse=_NOMINATIM_OK, wiki=None))
    ref = place_mod.resolve_place_reference(47.7, 11.5)
    assert ref is not None
    assert ref.town == "Bad Tölz"
    assert ref.extract == ""
    assert ref.source_url is None


def test_resolve_place_reference_none_when_geocode_fails(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(reverse=None, wiki=_WIKI_OK))
    assert place_mod.resolve_place_reference(47.7, 11.5) is None


def test_resolve_place_reference_prefers_given_location_over_geocode(monkeypatch):
    # The midpoint geocodes to a neighbouring municipality, but the hiker
    # said "Bad Tölz" — the given name wins for the town; the region is
    # still taken from the geocode.
    geo = {"address": {"village": "Wackersberg", "county": "Bad Tölz-Wolfratshausen"}}
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(reverse=geo, wiki=_WIKI_OK))
    ref = place_mod.resolve_place_reference(47.77, 11.54, location_name="Bad Tölz")
    assert ref is not None
    assert ref.town == "Bad Tölz"
    assert ref.region == "Bad Tölz-Wolfratshausen"
    assert "Isar" in ref.extract


def test_resolve_place_reference_given_location_survives_failed_geocode(monkeypatch):
    monkeypatch.setattr(place_mod, "_http_get_json", _fake_http(reverse=None, wiki=_WIKI_OK))
    ref = place_mod.resolve_place_reference(47.77, 11.54, location_name="Bad Tölz")
    assert ref is not None
    assert ref.town == "Bad Tölz"
    assert ref.region is None


def test_representative_coordinate_picks_midpoint():
    wps = [
        Waypoint(lat=1.0, lon=1.0, ele_m=0.0),
        Waypoint(lat=2.0, lon=2.0, ele_m=0.0),
        Waypoint(lat=3.0, lon=3.0, ele_m=0.0),
    ]
    assert place_mod.representative_coordinate(wps) == (2.0, 2.0)


def test_representative_coordinate_none_for_empty():
    assert place_mod.representative_coordinate([]) is None


# ── the stitch call (trailstory.llm.place) ───────────────────────────────────


def _ledger(
    *,
    chronology: list[Beat] | None = None,
    verbatim: list[str] | None = None,
) -> FactLedger:
    return FactLedger(
        people=[Person(name="Mia", role="baby in carrier")],
        weather="amazing weather",
        chronology=chronology
        if chronology is not None
        else [Beat(time_of_day="morning", activity="walk", objects_mentioned=["the church"])],
        verbatim_user_phrases=verbatim if verbatim is not None else [],
        where="Bad Tölz",
        when=datetime(2025, 5, 1, tzinfo=UTC),
        season="spring (May; northern hemisphere)",
        duration_min=180,
        distance_km=8.0,
        elevation_gain_m=400.0,
        summit_elev_m=900.0,
        n_photos=6,
    )


def _stitch_client(*responses: str | Exception) -> MagicMock:
    mock = MagicMock(spec=AnthropicClient)
    mock.complete.side_effect = list(responses)
    mock.model = "claude-haiku-4-5-test"
    return mock


_STITCH_OK = json.dumps(
    {
        "summary": {
            "en": "Bad Tölz is a town on the Isar in the Bavarian Prealps; you passed "
            "a small church on the way.",
            "ru": "(ru summary)",
            "de": "Bad Tölz liegt an der Isar in den Bayerischen Voralpen.",
        },
        "used_hiker_details": ["the church"],
    }
)


def test_place_beats_dedupes_objects_and_phrases():
    ledger = _ledger(
        chronology=[
            Beat(
                time_of_day="morning", activity="a", objects_mentioned=["the church", "the church"]
            ),
            Beat(
                time_of_day="afternoon", activity="b", objects_mentioned=["the lake", "the church"]
            ),
        ],
        verbatim=["a creepy old church", "the lake"],
    )
    assert place_beats_from_ledger(ledger) == ["the church", "the lake", "a creepy old church"]


def test_generate_place_context_happy_path():
    ref = PlaceReference(
        town="Bad Tölz",
        region="Bavarian Prealps",
        extract="Bad Tölz is a town in Bavaria.",
        source_url="https://en.wikipedia.org/wiki/Bad_Tölz",
        source_title="Bad Tölz",
    )
    client = _stitch_client(_STITCH_OK)
    ctx = generate_place_context(ref, ["the church"], client=client)
    assert ctx is not None
    assert ctx.town == "Bad Tölz"
    assert ctx.summary.en.startswith("Bad Tölz")
    assert ctx.summary.de.startswith("Bad Tölz")
    assert ctx.source_url == ref.source_url
    assert ctx.used_hiker_details == ["the church"]
    client.complete.assert_called_once()


def test_generate_place_context_includes_poi_matches():
    """ADR-019: POI matches reach the prompt and land on named_landmarks."""
    ref = PlaceReference(town="Bad Tölz", region="Bavarian Prealps", extract="Bad Tölz is a town.")
    client = _stitch_client(_STITCH_OK)
    matches = [PoiMatch(beat="the church", name="Mühlfeldkirche", category="church")]
    ctx = generate_place_context(ref, ["the church"], client=client, poi_matches=matches)
    assert ctx is not None
    assert ctx.named_landmarks == matches
    # The resolved real name was supplied to the stitch prompt.
    sent_prompt = client.complete.call_args.kwargs["prompt"]
    assert "Mühlfeldkirche" in sent_prompt


def test_generate_place_context_defaults_no_landmarks():
    ref = PlaceReference(town="Bad Tölz")
    ctx = generate_place_context(ref, ["the church"], client=_stitch_client(_STITCH_OK))
    assert ctx is not None
    assert ctx.named_landmarks == []


def test_generate_place_context_strips_code_fences():
    ref = PlaceReference(town="Bad Tölz")
    client = _stitch_client(f"```json\n{_STITCH_OK}\n```")
    ctx = generate_place_context(ref, [], client=client)
    assert ctx is not None
    assert ctx.summary.en.startswith("Bad Tölz")


def test_generate_place_context_retries_then_succeeds():
    ref = PlaceReference(town="Bad Tölz")
    client = _stitch_client("not json at all", _STITCH_OK)
    ctx = generate_place_context(ref, [], client=client)
    assert ctx is not None
    assert client.complete.call_count == 2


def test_generate_place_context_none_on_bad_json_twice():
    ref = PlaceReference(town="Bad Tölz")
    client = _stitch_client("nope", "still nope")
    assert generate_place_context(ref, [], client=client) is None
    assert client.complete.call_count == 2


def test_generate_place_context_none_on_invalid_schema():
    ref = PlaceReference(town="Bad Tölz")
    # Valid JSON object, but the summary is missing ru/de — LocalizedString
    # requires all three, so validation fails and the block is omitted.
    client = _stitch_client(json.dumps({"summary": {"en": "only english"}}))
    assert generate_place_context(ref, [], client=client) is None


def test_generate_place_context_none_on_llm_error():
    ref = PlaceReference(town="Bad Tölz")
    # A client-level error soft-fails to None on the first attempt, so the
    # block retries once; a second error gives up and omits the block.
    client = _stitch_client(LLMResponseError("boom"), LLMResponseError("boom again"))
    assert generate_place_context(ref, [], client=client) is None
    assert client.complete.call_count == 2
