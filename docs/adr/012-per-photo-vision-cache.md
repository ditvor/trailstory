# ADR 012 — Per-photo vision description cache (Phase 3.1)

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

[ADR-010](010-photo-grounding-via-vision.md) (Phase 3) added a
per-photo vision describer that runs on every render. For a 6-photo
hike that's six paid vision calls on every CLI re-run and every
template-iteration loop — wasted spend whenever the photos haven't
changed. The same `(photo bytes, vision model)` pair always produces
the same description; caching is the obvious fix.

The existing narrative cache (`trailstory/llm/cache.py`,
`~/.cache/trailstory/narratives/`) caches the final
`NarrativeOutput` keyed by the whole hike's content (seed text + GPX
bytes + every photo's bytes). A change to any one photo busts the
narrative cache for the whole hike, which is correct — but it also
forces re-describing every other photo even though their bytes
haven't moved. A per-photo cache fixes that.

---

## Options considered

### Option A — Separate per-photo cache, content-addressed (chosen)

Mirror the narrative cache's design: SHA-256 of `(photo bytes,
vision_model)`, JSON-serialised `PhotoDescription` written to
`~/.cache/trailstory/vision/<key>.json`. Read via `vision_cache.get`
before the LLM call, write via `vision_cache.put` after. Fail-soft on
OS errors (logged, ignored — cache failure cannot break a render).

**Pros:**

- Identical pattern to the existing narrative cache — the maintenance
  burden is shared and the design rationale is already documented.
- Per-photo granularity. A single new photo only invalidates that
  photo's cache entry; the other five hit cache and skip the LLM
  call entirely.
- No coordination with the narrative cache. Both can be cleaned or
  inspected independently. Tests can override either's directory
  via env var (`TRAILSTORY_CACHE_DIR`, `TRAILSTORY_VISION_CACHE_DIR`).
- Cache key includes vision model. Swapping models invalidates the
  cache for everyone cleanly, instead of serving stale Haiku-shaped
  descriptions to a Sonnet caller.
- Eval bypasses the cache (`use_cache=False`) so the goldens always
  reflect a live vision call. CLI keeps cache on by default.

**Cons:**

- Adds a third local cache directory if a future cache (e.g. ledger
  cache) lands. The naming convention (`narratives/`, `vision/`)
  scales to that.
- Schema drift in `PhotoDescription` invalidates all cached entries.
  The cache catches this via Pydantic validation failure on read
  (`vision_cache.get` returns None silently). Same recovery pattern
  as the narrative cache.

### Option B — Extend the narrative cache to also store per-photo descriptions

Stash the per-photo descriptions alongside the final narrative in a
single cache entry. One file per hike.

**Pros:** Fewer files on disk. One eviction policy to reason about.
**Cons:** Loses per-photo granularity (a new photo busts every
description). Couples the cache shape to two different model
schemas, making future evolution harder.

### Option C — In-memory only

LRU dict scoped to the running process. No disk persistence.

**Pros:** Simplest implementation.
**Cons:** Wins nothing on the CLI use case (each invocation is a
fresh process). The web app stays alive across renders but multiple
workers would have separate caches. Disk-backed is the only design
that actually pays off for v0 usage patterns.

---

## Decision

**Adopt Option A.** New module `trailstory/llm/vision_cache.py`
modelled directly on `trailstory/llm/cache.py`. New cache directory
`~/.cache/trailstory/vision/` (override via
`TRAILSTORY_VISION_CACHE_DIR` for tests). New `cache_key`,
`get`, `put` functions; identical fail-soft semantics; identical
Pydantic-validation-as-schema-version-check pattern.

The cache is wired into `trailstory.photos.describe_photos` behind
a `use_cache` kwarg (default `True`). Tests can pass `use_cache=False`
to assert the live behaviour without disk side-effects, and the eval
runner does this so goldens always come from a fresh vision call.

---

## Consequences

### What changes

- New `trailstory/llm/vision_cache.py` (~120 lines, mirrors `cache.py`).
- `trailstory/photos.py`:
  - `describe_photos` gains a `use_cache: bool = True` kwarg.
  - New private `_describe_one_with_cache` helper that does the
    cache lookup before the LLM call and writes through after.
- `trailstory/cli.py`: passes `use_cache=not no_cache` to
  `describe_photos` so the existing `--no-cache` flag now disables
  the vision cache too (same flag, both caches honour it).
- `tests/eval/run.py`: pins `use_cache=False` on its
  `describe_photos` call.
- Tests can monkeypatch `TRAILSTORY_VISION_CACHE_DIR` to scope cache
  writes to a tmpdir.

### What becomes easier

- A template / CSS iteration loop with the CLI is now near-free on
  the vision pass: first run pays for vision, every subsequent
  re-render hits the per-photo cache.
- Adding a third LLM-call cache (e.g. ledger cache, if profiling
  ever justifies it) follows the same template.

### What becomes harder

- One more place where stale data can hide. Schema drift in
  `PhotoDescription` invalidates entries automatically; semantic
  drift in the describer prompt does not (the cache key omits prompt
  text by design — same trade-off as the narrative cache, see
  ADR-003 and ADR-008's "known limitations").
- Photos modified out-of-band (e.g. EXIF rewrite by another tool)
  keep their old descriptions until the bytes change. Acceptable
  for v0; if it bites, the user can `rm -rf
  ~/.cache/trailstory/vision/` to force a refresh.
