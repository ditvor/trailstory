# ADR 017 — "About this place" block from grounded external sources

**Date:** 2026-06
**Status:** Proposed
**Decided by:** v0 product owner

---

## Context

The memory page tells the hiker's own story but says almost nothing about
*where* the hike happened. The primary audience — family abroad, a
grandparent in Russia, neighbours in Germany — often has never heard of the
place. A short, warm "about this place" note ("Bad Tölz, a town on the Isar
in the Bavarian Prealps…") makes the page feel like a postcard instead of a
GPS export, and answers the first question a distant reader has: *where is
this?*

The obvious implementation — "ask the writer to add a sentence about the
town" — is exactly what ADRs 007–016 forbid. The whole narrative pipeline
is an anti-fabrication machine: the writer consumes only a `FactLedger`
derived from seed + GPX + photos and is structurally unable to introduce a
fact that isn't in it (ADR-009). Place facts from a model's training data
are the textbook `UNSUPPORTED` category — and place facts are a classic LLM
failure mode (wrong river, wrong founding date, the wrong same-named town).
A confidently wrong place fact, read by someone who knows the area, is worse
than no note at all: it erodes the trust that is this product's wedge.

## Decision

Add an **"About this place"** block produced by a **separate LLM call**,
deliberately outside the ADR-009 two-pass pipeline, governed by one rule:

> **Source the place facts; don't generate them.** The model supplies only
> the prose that connects two already-grounded streams of fact. It may not
> add a third fact from its own knowledge.

The two grounded streams:

1. **External facts** — a reference extract fetched deterministically from
   the hike's GPS coordinates: reverse-geocode (OpenStreetMap Nominatim) →
   town + coarse region; the town's Wikipedia REST summary → a short factual
   extract with a citable source URL. No LLM touches this; it is real,
   attributable text.
2. **Lived detail** — the hiker's own place beats, pulled from the existing
   ledger (`chronology[*].objects_mentioned` ∪ `verbatim_user_phrases`).
   Already ledger-grounded; the "creepy church" the hiker actually wrote
   about, in their own words.

The stitch call (`SYSTEM_PLACE_CONTEXT` / `USER_PLACE_CONTEXT_TEMPLATE`)
receives both, weaves them into a 2–3 sentence tri-lingual note, and is
explicitly forbidden from adding any fact not present in one of the two
inputs. It reports the lived beats it used (`used_hiker_details`) as a cheap
audit hook, mirroring sentence-level provenance (ADR-014). The output is a
new `PlaceContext` model carried on `Memory` — **not** on `NarrativeOutput`,
so it neither bumps the narrative `schema_version` nor invalidates the
narrative cache, and it carries its own source attribution.

### Why a separate call, not a ledger field

The town facts come from an *external* source. Folding them into the ledger
would mean handing external knowledge to the extractor/writer, breaking the
ADR-009 contract that the writer sees only seed-derived facts. Keeping the
place call separate means the narrative stays exactly as grounded as before;
the place block is additive and isolable.

### Opt-in and privacy

Reverse-geocoding sends the hike's coordinates to an external service, which
is a location disclosure. Given "privacy as wedge", the feature is **off by
default**: a `--place` CLI flag (and `Settings.use_place_context`, default
`False`) turns it on per run. The coordinate sent is the track midpoint at
coarse zoom (town level), never a precise point. The reference text is
embedded into the HTML at build time, so the output stays self-contained and
offline (ADR-001) — the network is touched only during generation, which
already requires it.

### Soft-fail everywhere

No network, a 404, a hamlet with no article, a malformed response, or a
failed stitch all degrade gracefully: a missing Wikipedia extract still
yields a town-only one-liner ("Bad Tölz, a town in the Bavarian Prealps");
a failed geocode or failed stitch simply omits the block. The place block is
a nice-to-have and must never break a render.

### Attribution

Wikipedia content is CC BY-SA. The block renders a source link
(`source_url` / `source_title`) under the note. The extract is used as a
short factual summary, faithfully translated, not reproduced wholesale.

## Consequences

- New deterministic module `trailstory/place.py` (reverse geocode + Wikipedia
  fetch, urllib-based, no new runtime dependency) and LLM module
  `trailstory/llm/place.py` (the stitch). New prompts in `llm/prompts.py`.
- New `PlaceContext` model on `Memory`; `place_context` defaults `None`, so
  every existing `Memory` construction and persisted `state.json` stays
  valid.
- New `Settings.place_model` (Haiku-class default — the task is constrained
  stitching, not open creative writing; override via `PLACE_MODEL`) and
  `Settings.use_place_context`.
- All three visual styles (editorial / log / encyclopedia) gain a conditional
  "About this place" block, each in its own idiom.
- The town name prefers the hiker-supplied `location_name` over the geocoded
  result (a track midpoint can geocode to a neighbouring municipality —
  validated live: a Bad Tölz track midpoint resolved to Wackersberg).
  Reverse-geocoding only fills the coarse region.
- **Web builder wiring** (done in a follow-up): an opt-in checkbox on the
  builder form carries `use_place_context` through `prepare_pipeline`'s
  pending state; `stream_pipeline` resolves the block after the narrative
  completes, reusing the ledger it already extracted (a `ledger=` param
  added to `generate_narrative_stream`). A `place_client_factory` is injected
  like the other client factories, and the geocode+Wikipedia resolver is
  injected too (`place_reference_resolver`) so the fake-LLM dev mode and
  tests stay fully offline.
- **Not in this ADR / explicitly rejected for v1:**
  - *Per-language source extracts* (ru/de Wikipedia). v1 fetches the English
    summary and lets the stitch translate; localized extracts are a future
    refinement.
  - *POI name resolution* (matching the hiker's "creepy church" to a real
    OSM feature like "Leonhardikirche"). The match is genuinely fuzzy and a
    wrong identification is worse than a generic one; a separate ADR if we
    pursue it.
  - *Letting the writer weave place facts into the main narrative.*
    Reintroduces the fabrication risk the whole initiative removes.

If a future change wants place facts inside the narrative proper, or wants to
drop the supplied-source constraint, open a new ADR rather than relaxing the
rule here.
