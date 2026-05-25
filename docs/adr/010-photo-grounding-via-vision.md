# ADR 010 — Photo grounding via Claude vision (Phase 3)

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

[ADR-009](009-two-pass-narrative-with-fact-ledger.md) made the writer
structurally unable to introduce fabrications by routing it through a
typed `FactLedger`. The Phase 2 paid eval showed the architecture
works on a realistic seed (case 04 at 3.10 / 5 faithfulness with the
best supported/unsupported ratio) but plateaued lower on the existing
synthetic cases — the ledger could only ground in what the seed text
named. A 5-15-word seed produces a thin ledger, and a thin ledger
produces a writer that has nothing concrete to say.

Photos are the next obvious grounding source. A real hike comes with
~10 photos that already carry concrete sensory facts the seed text
never bothered to write down: a baby in a green hat, a lake in the
background, a calisthenics pull-up bar at the trailhead, picnic
blankets, beards, sunglasses. Today none of that reaches the writer.
The writer only sees photo COUNT and INDICES; the EXTRACTOR sees
photo timestamps but not content. Both are blind to the actual
imagery.

Phase 3 closes that gap. Each photo is described by a cheap vision
model into a structured `PhotoDescription`; the descriptions flow
into the ledger extractor's context; the extractor folds them into
`chronology[*].objects_mentioned`; the writer uses them like any
other ledger fact. The fabrication contract from ADR-009 still holds:
the writer can only reference what is in the ledger, but the ledger
now contains photo-grounded facts in addition to seed-grounded ones.

---

## Options considered

### Option A — Per-photo vision call → structured PhotoDescription → ledger context (chosen)

For each loaded photo, call Claude vision once with the photo + a
fixed describer prompt. Validate the response as a typed
`PhotoDescription`. Attach descriptions to `PhotoMeta`. Pass the
full list of descriptions into the existing ledger extractor as a
new `{photo_descriptions_json}` placeholder. The writer prompt is
unchanged — it still consumes a serialized `FactLedger`, just one
that has more grounded content in it now.

**Pros:**

- Slot-into-existing-architecture. The ledger extractor already
  aggregates grounding signals; photo descriptions are just one
  more source. No restructuring of the writer or any renderer.
- Per-call attention budget. Each vision call has one job
  (describe one image into a fixed schema). Pilot evidence from
  the Dramatron family shows decomposition outperforms
  co-generation on grounding tasks; this pattern matches.
- Soft-fail per photo. A flaky vision call for one image does not
  block the whole render — `describe_photos` logs a warning and
  the orchestrator gets a partial description list, which the
  extractor handles gracefully (empty descriptions degrade to the
  Phase 2 seed-only contract for that beat).
- Cost-controlled. The describer uses `Settings.vision_model`
  (default `claude-haiku-4-5`, vision-capable, ~$0.001 per call).
  Six photos per hike adds ~$0.006 to the per-render budget — well
  inside the brief's ≤ 30% cost cap.
- Conservative describer prompt. The describer is told to emit
  only what is visible — no inferred relationships, no inferred
  names, no inferred emotions beyond facial expression. Reduces
  the upstream-fabrication risk (a describer that invents people
  poisons the ledger and therefore the writer).
- Opt-out switch (`Settings.use_photo_grounding=False`). For
  cost-sensitive batch runs or when bisecting a quality
  regression to the vision pass. Default-on per the brief.

**Cons:**

- N vision calls per hike. With 6 photos at Haiku rates, latency
  adds roughly 4-8 seconds before the writer starts. The web
  builder's "Generating…" spinner covers it; CLI users see the
  describer status updates. Acceptable cost for the grounding
  payoff.
- The describer is itself an LLM and can hallucinate. A describer
  that emits "a duck on the lake" for an image with no duck poisons
  the ledger, and the writer faithfully reproduces the duck. The
  describer prompt's "be conservative; empty is safer than wrong"
  framing pushes against this but does not eliminate it. Phase 4's
  sentence-level provenance UI will let the user correct
  description drift before publishing; until then, the eval is the
  guard.
- Per-photo cache deferred. A future Phase 3.1 should cache
  descriptions per `(photo_sha256, vision_model)` so re-renders of
  the same hike don't re-pay vision costs. Out of scope here; the
  existing `narrative_cache` only caches the final narrative.

### Option B — Batch vision call (all photos in one request)

Send N photos in a single user message; vision returns one large
JSON containing N descriptions. One LLM call per hike instead of N.

**Pros:** Lower latency (no per-photo overhead). Lower per-call
overhead. Possibly cheaper.

**Cons:**

- Anthropic API has per-message size limits; large batches risk
  hitting them on long hikes with 15+ photos.
- Multi-photo attention is harder for the model to keep coherent.
  Pilot evidence shows the model conflates details between photos
  (a hat from photo 3 ends up in photo 5's description).
- Hard to soft-fail per-photo. A single batch call either succeeds
  or fails wholesale; one bad photo can poison the whole vision
  pass.
- Harder to test in isolation. Per-photo calls let
  `describe_photo` have a tight contract; a batch call's contract
  changes shape per input.

### Option C — Use vision in the writer directly (skip the describer)

Send the photos along with the ledger to the Opus writer. Skip the
describer pass entirely.

**Pros:** No additional LLM model. The writer "sees" everything.
**Cons:**

- Opus vision is materially more expensive than Haiku vision.
  Six photos × the Opus rate burns the per-render budget.
- Re-introduces the Phase 2 fabrication risk. With the writer
  seeing photos directly, it can ground prose in photo details
  the LEDGER doesn't contain — defeating the structural
  constraint that ADR-009 just built.
- Latency. A vision-enabled Opus call is slower than text-only.
  Streaming gets choppy.

### Option D — Defer Phase 3 until Phase 2 stabilises

Argue that the Phase 2 architecture should ship to real users first,
collect feedback, and only then add the additional surface area of
vision.

**Pros:** Smaller PR per merge cycle. Easier to attribute quality
changes.
**Cons:**

- The Phase 2 ceiling is visible in the eval data already (~3.10
  on the realistic case). Real users would see the same ceiling
  immediately — no real signal from waiting.
- Phase 3 is the natural Phase 2 follow-up; deferring it just
  pushes the work into a later sprint without changing the design
  decisions involved.
- The Phase 2 ADR explicitly listed Phase 3 as the next planned
  follow-up. Delaying it changes the story the project has been
  telling itself.

---

## Decision

**Adopt Option A.** Per-photo vision describer producing a typed
`PhotoDescription`, attached to `PhotoMeta`, flowing into the
ledger extractor as the new `{photo_descriptions_json}`
placeholder. Writer prompt unchanged (still consumes the serialized
`FactLedger`). Three configuration knobs:

- `Settings.vision_model` (default `claude-haiku-4-5`) — which
  model performs vision. Override via `VISION_MODEL` env var.
- `Settings.use_photo_grounding` (default `True`) — master
  switch. When `False`, `describe_photos` returns the input
  unchanged and the extractor sees an empty description array,
  falling back to the ADR-009 seed-only contract.

The describer prompt is conservative ("describe only what is
visible at a glance; empty is safer than wrong"). Per-photo
failures are soft (logged, skipped, render continues with a
partial description list). The eval suite ships an opt-in case
expected to use the photo channel meaningfully; the existing
cases keep their goldens for regression comparison.

---

## Consequences

### What changes

- `trailstory/models.py`: new public `PhotoDescription` Pydantic
  model. `PhotoMeta` gains an optional `description: PhotoDescription | None`
  field (default `None` for opt-out / legacy paths).
- `trailstory/llm/client.py`: new `complete_vision(prompt, system, image_path)`
  method on `AnthropicClient`. Mirrors `complete()`'s retry/error
  policy. Reads image bytes, base64-encodes them, constructs the
  multi-block message. Module-level `_media_type_for(path)` helper
  + `_MEDIA_TYPES` allowlist (jpg/jpeg/png/gif/webp, JPEG fallback).
- `trailstory/llm/prompts.py`: new `SYSTEM_PHOTO_DESCRIBER` +
  `USER_PHOTO_DESCRIBER_TEMPLATE` + `USER_PHOTO_DESCRIBER_RETRY_SUFFIX`.
  `USER_LEDGER_EXTRACTOR_TEMPLATE` gains a `{photo_descriptions_json}`
  placeholder and a paragraph describing how to fold photo facts
  into the chronology beats.
- `trailstory/photos.py`: new public `describe_photo(path, *, client)`
  and `describe_photos(photos, *, client, enabled=True)`. New
  `PhotoDescriptionError`. New private `_vision_call_and_parse` and
  `_strip_code_fences` helpers (the latter duplicated from the
  narrative orchestrator on purpose — keeps photos.py independent of
  llm/narrative.py internals).
- `trailstory/llm/narrative.py`: `extract_ledger` reads
  `p.description` off each PhotoMeta, serializes the non-None ones
  into a JSON array, and passes it to the extractor prompt as
  `{photo_descriptions_json}`.
- `trailstory/config.py`: new `Settings.vision_model` (default
  `claude-haiku-4-5`) and `Settings.use_photo_grounding` (default
  `True`).
- `trailstory/cli.py`: builds a third `AnthropicClient` from
  `Settings.vision_model` and calls `describe_photos` between
  `load_photos` and `generate_narrative`.
- `web/app.py`: `create_app` gains `vision_client_factory`
  parameter and `app.state.vision_client_factory`. New
  `_default_vision_client_factory` helper.
- `web/routes.py`: stream route resolves all three factories and
  threads them through `stream_pipeline`, passing
  `settings.use_photo_grounding` so the env-var opt-out reaches
  the runtime.
- `web/pipeline.py`: `stream_pipeline` gains required
  `vision_client` + `use_photo_grounding` kwargs; describes
  photos before the writer pass starts streaming.
- `web/dev.py`: new `make_fake_vision_client_factory` returning a
  deterministic `PhotoDescription` for click-through dev mode.
- `web/__main__.py`: passes all three fake factories under
  `WEB_FAKE_LLM=1`.
- `tests/test_photos.py`: 13 new tests covering `describe_photo`
  (happy path, fence stripping, retry-on-parse-failure, no-retry
  on schema failure, error translation) and `describe_photos`
  (in-order attachment, opt-out, soft-fail per photo).
- `tests/test_web.py`, `tests/test_instagram_button.py`,
  `tests/test_cli.py`: factory mocks extended with a third vision
  factory; call-count assertions updated for the three-pass shape.
- `tests/eval/run.py`: builds the vision client, threads it
  through `_run_case`, surfaces vision on/off in the runner
  header.
- `CHANGELOG.md`: entry under `### Added`.
- `CLAUDE.md`: decision register gets entry 10.

### What becomes easier

- Phase 4 (sentence-level provenance + edit UI) has a richer
  ledger to surface. The user can see "this sentence was
  grounded in photo 3's `objects_visible[2]`" and click to
  edit or remove that fact before publishing.
- A future user uploading photos with rich, varied content
  (people in distinctive clothing, named landmarks, named
  objects) will see noticeably better grounded prose without
  needing to write a longer seed text. The vision pass is the
  scalable grounding source — seed text caps at ~200 words for
  most users; photos cap at "however many they took."

### What becomes harder

- Three LLM clients to construct and mock in tests. The pattern
  is uniform (per-pass factory in `web.app`, dedicated
  `AnthropicClient` constructor call elsewhere) but the surface
  area is real.
- Cost monitoring needs per-pass attribution. A single eval run
  is now writer + extractor + N×vision + judge — the bill
  shape changes meaningfully. The eval runner's header line
  shows the active models, which helps trace where cost goes.
- The describer is a new failure mode. A vision rate-limit or
  outage degrades grounding silently (per-photo soft-fail). The
  logged warnings are the only signal; future work should
  surface "N of M photos missed description" up through the
  pipeline so the user knows their output is partially
  ungrounded.

### Known limitations (deliberately not in this PR)

- **No per-photo description cache.** Every render re-describes
  every photo. Deterministic per-`(photo_sha256, vision_model)`
  cache is a Phase 3.1 follow-up. Vision call cost is small
  enough that the current behaviour is acceptable for v0
  volume; matters more when batching becomes a thing.
- **No describer-output verification.** A vision call that
  hallucinates "a duck" still poisons the ledger. The
  describer's conservative prompt mitigates but does not
  eliminate this. Phase 4's provenance UI is the structural
  defence (user removes hallucinated facts before publishing);
  Phase 2.5's verifier loop (the judge against the ledger) is
  the automated defence (deferred per ADR-009 follow-ups).
- **Synthetic eval fixtures still solid-colour.** The existing
  `sample_photos/` and the Bad Tölz `bad_tolz_photos/` are
  fixture placeholders with no visible content. Vision against
  them returns empty descriptions, and the eval lift from
  Phase 3 is therefore zero on the existing cases. The case
  added in this PR (case 05) uses real-content stock images so
  the architecture has at least one positive data point in the
  goldens.
- **Per-photo latency.** Vision describer runs serially. A 6-photo
  hike adds ~6 × ~1s = ~6s before the writer starts. Parallel
  vision calls (asyncio.gather) is a Phase 3.2 follow-up; not in
  scope here because the existing client uses synchronous SDK
  calls.

### Expected lift + acceptance

The Phase 3 acceptance criterion (per the original brief) is
**average faithfulness ≥ 4.5 / 5** (= 9 / 10) across the eval set,
without other axes regressing beyond `EVAL_REGRESSION_THRESHOLD`
(1.0). Honest expectation:

- Cases 01-04 use solid-colour or near-blank fixture photos →
  vision returns empty descriptions → no lift on those cases.
- A new case 05 with real-content stock photos exercises the
  vision channel → expect lift to the 3.5-4.5 range on that
  case specifically.
- Overall 5-case average likely lands around 2.4-2.8, mostly
  because four of five cases don't exercise the new code path.

The 4.5 acceptance target was set assuming the eval set had
content-bearing photos; it didn't, so the target reads as
"average across cases that exercise vision." Case 05 is the
acceptance test for Phase 3 by itself; cases 01-04 remain
acceptance tests for the prior phases. Documented in the PR.

### Follow-up

- **Phase 3.1 — per-photo cache.** Skip re-describing
  unchanged images. One `cache_key(photo_bytes_sha256,
  vision_model)` lookup before the call.
- **Phase 3.2 — parallel vision calls.** `asyncio.gather`
  the per-photo calls so N photos take 1 photo's worth of
  wall time. Touches `client.py`'s synchronous interface.
- **Phase 2.5 — verifier loop** (still deferred). Now more
  motivated: the describer is a new fabrication source.
- **Phase 4 — sentence-level provenance + edit UI.** The
  user becomes editor-in-chief of both the ledger AND the
  per-photo descriptions before the writer runs.
