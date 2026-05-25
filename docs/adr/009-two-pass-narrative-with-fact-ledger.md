# ADR 009 — Two-pass narrative pipeline with a structured FactLedger

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

[ADR-007](007-faithfulness-eval-axis.md) made fabrication measurable and
recorded a 1.32 / 5 baseline across the three eval cases. Two-thirds of
every concrete claim in current narratives was unsupported by the seed
text. [ADR-008](008-writer-prompt-temporal-grounding-and-anti-fabrication.md)
tried prompt-only fixes — adding GPX-derived date / season and an
explicit anti-fabrication clause. The lift landed at +0.41 average
(1.32 → 1.73), below the ≥ 0.5 acceptance target, with case 03
regressing by -0.34. That number is the data: **prompt engineering
alone caps out around +0.5 on this task.** The fabrication problem is
structural, not a prompt-tuning gap.

Read the writer prompt that produced the Phase 0 / Phase 1 baselines
and the failure mode is obvious: the model sees a free-form seed text
("The fog cleared just as we reached the ridge."), 1000+ tokens of
hike data, and an instruction to write 3-5 grounded paragraphs in
three languages. With that little anchoring, "grounded" decays to
"plausible." A short seed produces long prose; the gap fills with
training-data defaults. Adding "don't invent ducks" to the prompt
slows the duck arrival; it does not stop it.

The pilot Bad Tölz hike that motivated this whole initiative
illustrates: a 100-word seed text became a 4-paragraph output with
~40% of its concrete details invented (the duck, the chopsticks, the
"summer-milky" April river, Olga gasping, Igor laughing-then-stopping).
The writer is doing what writers do — filling space — and the seed
text is too thin to fill it grounded.

The structural fix is to **change what the writer sees**. The writer
should not read the seed text. The writer should read a structured
ledger of facts extracted from the seed (and the GPX, and the photo
metadata) by a cheap upstream pass. If the ledger does not contain a
duck, the writer literally cannot reference one — there is nothing in
its context to draw a duck from.

This pattern has a name in the AI-writing literature: **hierarchical
decomposition** (DeepMind's Dramatron, 2024; "Beyond Direct
Generation," Oct-2025 arXiv 2510.23163). It is also how investigative
journalists work in meatspace: interview, write structured notes, then
write the article *from the notes*. The contract is "I cannot publish
what I did not record."

---

## Options considered

### Option A — Two-pass with a typed FactLedger; writer sees only the ledger (chosen)

Add `extract_ledger(hike_input, gpx_stats, photos, *, client)` as the
first pass. A cheap Haiku-class model reads the seed text + photo
timestamps and emits a structured `_ExtractorOutput` (people, weather,
chronology). Python merges in the deterministic fields (GPX stats,
derived season, photo count) and validates the whole as a
`FactLedger`. The writer's prompt is rewritten to take a serialized
ledger as its sole input — no `seed_text` placeholder.

The structural guarantee: the writer cannot reference a noun the
ledger does not contain.

**Pros:**

- Structural, not behavioural. Removes the fabrication source instead
  of asking the model nicely to stop. A prompt-level "do not invent
  ducks" loses to training-data prior; a typed `chronology[*].objects_mentioned`
  field that does not contain "duck" cannot be inflated post-hoc.
- Cost-asymmetric in the right direction. Extractor uses a cheap fast
  model (Haiku); writer keeps Opus. The expensive call's quality
  matters; the cheap call's structure matters. Total per-render cost
  rises by ~5-10% (one Haiku call on the order of $0.001 added to an
  Opus call on the order of $0.02-0.04), well inside the ≤ 30% budget
  the brief stated.
- The ledger is human-readable and human-correctable. Once Phase 4
  ships (sentence-level provenance UI), the same ledger structure
  gives users a list of facts to confirm / edit / remove before the
  writer runs.
- Tests get easier, not harder. The extractor's contract is one small
  JSON shape; the writer's contract is one small ledger consumer.
  Each pass is testable in isolation against a mocked client. The
  combined `generate_narrative` test surface barely grows.
- Renderers ([html.py], [instagram.py]) are unchanged — they consume
  `Memory`, which embeds `NarrativeOutput`, which still has the same
  shape. The two-pass restructure is internal to the LLM layer.

**Cons:**

- An entire pass can fail. We now have two LLM failure modes (ledger
  and writer) instead of one. Funnelled into a single
  `NarrativeGenerationError` so callers above the LLM layer only
  handle one exception type, but the internal complexity is real.
- The writer loses access to the seed's tone / voice. A future
  observation may be that prose now reads stiffer or more uniform
  across hikes because every writer sees a ledger in roughly the same
  shape, not the hiker's own sentences. If so, Phase 2.1 can pass a
  short "voice snippet" alongside the ledger as advisory context. Not
  in scope for this ADR; deferred until eval data shows the gap.
- Extractor latency lands before the first stream byte. The web
  builder's SSE flow now has a 1-2 second extra delay before the
  writer starts streaming chunks. Acceptable — the existing "Generating…"
  spinner covers it — but worth noting.
- Cache key still omits prompt text (the existing ADR-003 quirk). A
  CLI re-render of an old hike may serve pre-ADR-009 output from
  cache. Tracked in ADR-008's "known limitations" follow-up; not
  re-litigated here.

### Option B — One LLM call, structured-output mode forcing a JSON-with-ledger response

Modern Anthropic SDK supports tool-call-style structured output. We
could ask the writer to produce a single JSON containing both an
inline ledger and the narrative, all in one call.

**Pros:** One LLM call. No second client to wire up. Cheapest.
**Cons:**

- Defeats the structural guarantee. If the writer produces the ledger
  AND the prose in one pass, it can invent both. The whole point is
  that an upstream extractor commits to a ledger BEFORE the writer
  has a chance to make things up. Co-generating them puts the ledger
  back inside the writer's context window where prose-fabrication
  energy lives.
- The model's attention is split between two structurally different
  tasks (extraction vs writing). Pilot evidence in the Dramatron
  family of work shows decomposition outperforms co-generation on
  exactly this kind of faithfulness contract.

### Option C — Skip Phase 2, jump to Phase 3 (multimodal photo grounding)

Argue that adding photo content via Claude vision would add real
grounding facts that the writer could use, closing the fabrication
gap without needing the structural ledger pass.

**Pros:** Photos add genuinely new grounding signal. The writer's
"what does the day look like" question gets a real answer.
**Cons:**

- Photos add grounding signal but do not constrain the writer's
  output. The writer would still produce a 4-paragraph piece for a
  short seed; photos would just be one more source it can invent
  around. The fabrication mechanism is "thin source + verbose
  output"; photos don't change that ratio.
- Vision calls are more expensive per call than Haiku text calls.
  Better to first reduce fabrication structurally (Phase 2) and then
  add grounding richness (Phase 3) on top of the constrained writer.
- The two phases are complementary, not alternatives. Phase 2's
  ledger architecture is exactly what Phase 3's photo-description
  output should flow into. Skipping Phase 2 leaves Phase 3 without a
  natural place to land the per-photo facts.

---

## Decision

**Adopt Option A.** The narrative pipeline becomes a two-pass flow:

1. `extract_ledger(hike_input, gpx_stats, photos, *, client)` — cheap
   pass on `Settings.ledger_model` (default `claude-haiku-4-5`). LLM
   produces a small JSON of people / weather / chronology; Python
   merges in deterministic fields and validates as `FactLedger`.
2. `generate_narrative(...)` (and `generate_narrative_stream(...)`)
   — Opus pass consuming the serialized `FactLedger` as its sole
   input. The writer prompt has new placeholders `{ledger_json}` +
   `{n_photos}` + `{n_photos_minus_1}`; `seed_text` no longer reaches
   the writer.

Both entry-point functions accept a new required `ledger_client`
kwarg (separate from the existing `client`, which is the writer).
The CLI, the web pipeline, and the eval runner each construct two
clients from `Settings.model` and `Settings.ledger_model`. The web
app's `create_app` factory gains a sibling `ledger_client_factory`
parameter and a sibling `_default_ledger_client_factory`.

The writer's anti-fabrication enforcement reads off the ledger's
structure: the prompt instructs that specific animals / foods / named
objects must appear somewhere in `chronology[*].objects_mentioned`,
the `where` field, or a person's role — and the model has no other
specifics to draw from because it is not given any.

---

## Consequences

### What changes

- `trailstory/models.py`: three new public Pydantic models —
  `Person`, `Beat`, `FactLedger`. All frozen. `FactLedger` carries
  both LLM-derived fields (people, weather, chronology) and
  deterministic fields (where, when, season, distances, summit,
  n_photos) so the writer has a single typed input.
- `trailstory/config.py`: new `Settings.ledger_model` field, default
  `claude-haiku-4-5`, overridable via `LEDGER_MODEL` env var.
- `trailstory/llm/prompts.py`: new `SYSTEM_LEDGER_EXTRACTOR` and
  `USER_LEDGER_EXTRACTOR_TEMPLATE` constants. `USER_NARRATIVE_TEMPLATE`
  rewritten to take `{ledger_json}` + photo counts only. Previous
  ADR-008 prompt preserved as a dated comment for revertability.
- `trailstory/llm/narrative.py`: new public `extract_ledger`
  function, new `LedgerExtractionError`, new private
  `_ExtractorOutput` model, new `_call_and_parse_with_system` and
  `_hike_start_datetime` helpers. `generate_narrative` and
  `generate_narrative_stream` rewritten as two-pass orchestrators
  with new required `ledger_client` kwarg.
- `trailstory/cli.py`: builds a second `AnthropicClient` from
  `Settings.ledger_model` and passes it as `ledger_client`.
- `web/app.py`: `create_app` gains `ledger_client_factory` parameter
  and `app.state.ledger_client_factory`. New
  `_default_ledger_client_factory` helper.
- `web/routes.py`: stream route resolves both factories from app
  state and threads both clients through.
- `web/pipeline.py`: `stream_pipeline` gains required `ledger_client`
  kwarg.
- `web/dev.py`: new `make_fake_ledger_client_factory` for `--fake-llm`
  dev mode. Constant ledger JSON matching the existing constant
  narrative for end-to-end click-through testing without API calls.
- `web/__main__.py`: passes both fake factories under
  `WEB_FAKE_LLM=1`.
- `tests/test_ledger.py`: 17 new tests covering `FactLedger` /
  `Person` / `Beat` validation and the `extract_ledger` happy path,
  retry policy, schema-validation failures, client-level errors, and
  the deterministic-merge behaviour.
- `tests/test_narrative.py`: existing 27 tests updated for the new
  two-client shape via a shared `_ledger_client()` helper; 4 new
  tests cover the ADR-009-specific behaviours (writer no longer sees
  seed_text; season and date flow through the ledger to both passes;
  ledger-only constraint phrasing in the writer prompt).
- `tests/test_prompts.py`: drift tests updated for the new
  `{ledger_json}` writer placeholder; sample fields now build a
  representative ledger.
- `tests/test_web.py`: shared `_make_ledger_client()` helper added;
  every `create_app(...)` call site now passes
  `ledger_client_factory`.
- `tests/test_instagram_button.py`: same shape.
- `tests/test_cli.py`: `_make_fake_client` dispatches on system
  prompt to return ledger JSON or narrative JSON depending on which
  pass is calling; call-count assertions updated for the 2-calls-
  per-pipeline shape.
- `tests/eval/run.py`: builds and threads a second `AnthropicClient`
  from `settings.ledger_model`; header line includes both model
  names for traceability.
- `CHANGELOG.md`: entry under `### Changed` and `### Added`.
- `CLAUDE.md`: decision register gets entry 9 pointing here.

### What becomes easier

- Phase 3 (multimodal photo grounding) has a natural home: photo
  descriptions extracted via vision flow into `extract_ledger`'s
  inputs and into `chronology[*].objects_mentioned`. No further
  restructuring needed.
- Phase 4 (sentence-level provenance + edit UI) gets a ready-made
  surface: the user can see and edit the ledger before the writer
  runs. The ledger is human-readable JSON, not opaque tokens.
- A future "show the user why the writer wrote what it wrote" feature
  has a clean answer: show the ledger.
- Cost tuning per pass becomes one config flip. Want to ship a
  cheaper variant? Override `MODEL=claude-sonnet-4-6` and keep
  `LEDGER_MODEL=claude-haiku-4-5`.

### What becomes harder

- Two LLM call paths to mock in tests instead of one. The `_ledger_client()`
  shared helper plus the system-prompt-dispatching `_make_fake_client`
  in test_cli keep this manageable, but it is more surface area.
- The extractor is now part of the latency budget. A slow Haiku
  region or a rate-limited account can stall narrative generation
  even before the writer starts. The existing client-level retry
  policy applies, but the worst-case end-to-end latency is now
  (writer p99 + extractor p99) instead of just (writer p99).
- The fake-LLM dev mode now needs two consistent fixtures. The
  ledger fixture and the narrative fixture in `web/dev.py` must
  stay coherent (the same hike, the same people, the same beats);
  if one drifts the other gets weird. Documented as a small ongoing
  maintenance cost.

### Known limitations (deliberately not in this PR)

- **Voice/tone loss from removing seed_text from the writer's
  context.** The writer sees a structured ledger and produces prose
  in a default register, not in the hiker's specific voice. If
  goldens show stiffness after this lands, Phase 2.1 can add a short
  "voice snippet" (one sentence from the seed, untouched) as
  advisory context to the writer — without breaking the
  fabrication guarantee, because it's clearly advisory rather than
  factual. Defer until eval data shows the gap.
- **CLI cache invalidation.** Same as ADR-008's "known
  limitations" — the cache key omits prompt text, so a pre-ADR-009
  cached entry could serve stale output on a CLI re-render of the
  same hike. Web builder uses streaming (bypasses cache). Tracked
  for a one-line `PROMPT_VERSION` bump before Phase 3.
- **Verifier loop deferred.** The brief mentioned an optional third
  pass: judge the writer's output against the ledger and retry once
  on a low faithfulness score. Left out of Phase 2 to keep scope
  contained. The judge already runs in the eval layer; promoting it
  into production is a Phase 2.5 decision based on measured Phase 2
  results.

### Expected lift + acceptance

The Phase 2 acceptance criterion is **average faithfulness ≥ 4.0 /
5** (= 8 / 10) across the three eval cases without other axes
regressing beyond `EVAL_REGRESSION_THRESHOLD` (1.0). Pilot
expectation from the cross-paper survey:

| case | Phase 1 | Phase 2 expected | Phase 2 best case |
|---|---:|---:|---:|
| 01-fixture-baseline | 1.09 | 3.5 | 4.3 |
| 02-joyful-summit | 2.36 | 4.0 | 4.6 |
| 03-exhausted-foggy | 1.74 | 3.8 | 4.4 |
| **average** | **1.73** | **3.8** | **4.4** |

Actual numbers go into the refreshed goldens at PR time. If the
average lands **below 3.0**, that's a signal the ledger schema or
the extractor prompt needs another iteration before Phase 3 can
build on it. If it lands **above 4.5**, the writer's stiffness
tax may not be worth the gain and Phase 2.1 voice-snippet support
should ship before any production users see the output.

### Follow-up

- **Phase 3 (multimodal photo grounding).** Add `describe_photo(path)
  -> PhotoDescription` to `trailstory/photos.py` using Claude vision.
  Photo descriptions flow into `extract_ledger`'s inputs so
  `chronology[*].objects_mentioned` reflects actual photo content,
  not just what the seed text named.
- **Phase 2.5 verifier loop** (conditional on Phase 2 results). Run
  the judge's faithfulness check against the ledger before returning
  to the user; on low score, regenerate once with the verdict list
  as feedback.
- **Cache version bump.** Add `PROMPT_VERSION` to the cache key so
  CLI re-renders pick up prompt changes. Out of scope here; tracked
  for Phase 3 alongside the vision additions.
