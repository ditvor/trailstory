# ADR 019 — POI name resolution for the place block

**Date:** 2026-06
**Status:** Proposed
**Decided by:** v0 product owner

---

## Context

ADR-017 gives the reader an "about this place" block grounded in two sources:
the town's Wikipedia extract and the hiker's own ledger beats. The lived beats
are necessarily *generic*: the ledger records "the wax-figure church", "a
creepy old church", "a lake" — the words the hiker used. The block can say
*"you passed a strange church"* but cannot **name** it, because no source in
the ADR-017 pipeline knows the church's real name.

But the name is knowable, and from a real source: the track ran past it, and
OpenStreetMap has named the feature. Resolving "the wax-figure church" near a
Bad Tölz track to its actual name (the Mühlfeldkirche, famous for exactly those
wax figures) turns a vague beat into a specific, verifiable detail — the
difference between a postcard and a guess.

The ADR-017 anti-fabrication framing was explicit that this is a *separate*,
later decision: *"POI name resolution … the match is genuinely fuzzy and a
wrong identification is worse than a generic one; a separate ADR if we pursue
it."* This is that ADR.

## The core risk

The matching is fuzzy. "The wax-figure church" is the hiker's subjective
description; the OSM feature is "Mühlfeldkirche". Connecting the two is a
heuristic, and **a wrong match is worse than no match** — naming the wrong
church is a confident falsehood, exactly the failure mode the whole place
feature is built to avoid. So the governing rule is:

> **Resolve conservatively. When the match is not unambiguous, skip it.** A
> generic "the old church" is an acceptable outcome; "the Leonhardikirche"
> when it was actually the Mühlfeldkirche is not.

## Decision

Add an opt-in POI resolution step to the place pipeline. It is **deterministic**
(no LLM does the matching — the name comes from OSM, a real source) and feeds
the resolved names into the existing place stitch as additional grounded facts.

1. **Fetch** named OSM features near the track via the Overpass API — a fixed
   set of "landmark" categories a hiker would actually mention (places of
   worship, peaks, lakes, alpine huts, castles/ruins, waterfalls, viewpoints).
   Bounded by the track's bounding box (slightly padded), then filtered to
   features within a tight radius of the track polyline.
2. **Categorise** each hiker beat by keyword (English + German, since the
   audience is Bavaria-centric): "church"/"kirche"/"chapel" → place_of_worship,
   "lake"/"see" → water, "peak"/"summit"/"gipfel" → peak, etc. A beat with no
   recognised category is not a POI and is skipped.
3. **Match** conservatively: among the fetched named features of the beat's
   category near the track, a match is made **only if exactly one** candidate
   exists. Zero candidates → nothing to name. Two or more → ambiguous → skip.
   This single-candidate rule is the entire safety guarantee.
4. **Feed** the resolved `beat → name` pairs into the place stitch as a new,
   clearly-labelled grounded input. The stitch may use the real name; it is
   still bound by the ADR-017 contract (the name is a supplied fact from OSM,
   not the model's invention). The matches are stored on `PlaceContext`
   (`named_landmarks`) for transparency and attribution.

### Why deterministic matching, not an LLM

An LLM could match more flexibly, but it would also *fabricate* matches
confidently when the right answer is "none". Keyword-category + single-candidate
is dumber and that is the point: it fails closed. The LLM's only job remains
prose, over facts it was handed.

### Opt-in, and scope

- Off by default (`Settings.use_poi_resolution`, and a CLI `--poi` flag that
  implies the place block). It adds a second external dependency (Overpass) and
  a fresh network round-trip, and — like reverse-geocoding — discloses the
  track's location to another service.
- **This PR wires POI into the CLI place path only.** The web-builder path is a
  deferred follow-up, exactly as the base place block shipped CLI-first
  (#63) before its web wiring (#65). The web `_resolve_place_for_stream` passes
  no POI matches in v1.

### Soft-fail and attribution

Every failure soft-fails to "no matches": Overpass down, a timeout, a malformed
response, an ambiguous or empty category all yield an empty match list, and the
block falls back to the ADR-017 generic behaviour. OSM data is ODbL; when a
resolved name is used, the rendered source line credits OpenStreetMap alongside
the Wikipedia attribution.

### Validation (live, Bad Tölz)

Run against the real Overpass API on the Bad Tölz fixture track, the query
found 13 named features nearby — and the matcher returned **no match**,
because the town is church-dense (10 churches within range). That is the
single-candidate rule working as designed: with ten churches, "the wax-figure
church" cannot be resolved unambiguously, so it is left generic rather than
guessed. The feature therefore helps most in *sparse* surroundings (one
church, one peak, one lake near the route) and correctly declines in dense
ones. The same run also surfaced a real bug — a mosque tagged only
`amenity=place_of_worship` was being labelled "church" — fixed by requiring
`religion=christian` (or a `building=church` tag) for the church label.

## Consequences

- New deterministic module `trailstory/poi.py` (Overpass fetch + keyword
  matching, stdlib `urllib`, single network seam for tests, no new dependency).
- New `PoiMatch` model; `PlaceContext.named_landmarks: list[PoiMatch]`.
- The place stitch (`SYSTEM_PLACE_CONTEXT` / `USER_PLACE_CONTEXT_TEMPLATE`)
  gains a `poi_matches` input and a clause permitting the supplied real names.
  `generate_place_context` gains a `poi_matches` parameter (defaults empty — the
  web path and every existing caller are unaffected).
- New `Settings.use_poi_resolution` + a CLI `--poi` flag.
- Editorial / log / encyclopedia templates add an OSM credit to the source line
  when `named_landmarks` is non-empty.
- The place eval net (ADR-017 / PR #66) gains a check that any named landmark
  the stitch uses traces to a supplied match (faithfulness for POI names).

## Explicitly out of scope / rejected for v1

- *Fuzzy / multi-candidate matching* (nearest-of-several, string similarity to
  the OSM name). Too easy to be confidently wrong; single-candidate only.
- *LLM-driven matching.* Reintroduces the fabrication risk.
- *Web-builder wiring.* Deferred follow-up.
- *POIs the hiker did not mention.* We only name what the hiker already
  referenced — we do not annotate the route with every church it passed.

If a future change wants fuzzier matching or to name un-mentioned features, open
a new ADR rather than loosening the single-candidate rule here.
