# ADR 011 — Verifier loop using self-reported provenance (Phase 2.5)

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

[ADR-009](009-two-pass-narrative-with-fact-ledger.md) (Phase 2) made
fabrication structurally impossible: the writer reads only the
`FactLedger`, never the seed text. [ADR-010](010-photo-grounding-via-vision.md)
(Phase 3) added per-photo vision so the ledger has more grounded facts
to draw on. [ADR-014](014-sentence-level-provenance-and-html-hover.md)
(Phase 4) adds sentence-level provenance tags so the writer self-reports
which sentences are SEED / PHOTO / GPX grounded vs INFERRED literary
reconstruction.

The provenance tags create a cheap, free signal that the orchestrator
can use to gate the writer's draft before returning it. If the writer
itself believes too many sentences are INFERRED, the user almost
certainly will too. Regenerate once with feedback, accept if the share
drops.

The original Phase 2.5 brief proposed promoting the paid LLM judge into
the production runtime ("run the judge against the ledger before
returning"). The Phase 4 provenance tags obviate that: the writer's own
labels are free, instant, and (per the prompt's explicit honesty
framing) reasonably calibrated. Production no longer needs to pay for a
judge call to catch the kind of regression the judge would have caught.

---

## Options considered

### Option A — Self-reported provenance ratio as the verifier signal (chosen)

After the writer pass returns a validated `NarrativeOutput`, count the
share of sentences tagged `ProvenanceSource.INFERRED`. If the share
exceeds `Settings.max_inferred_ratio` (default `0.5`), regenerate once
with feedback ("your previous draft tagged 64% of sentences as
INFERRED, exceeding the 50% ceiling; lean harder on seed / photo / gpx
sentences"). Keep the regenerated draft only if its INFERRED ratio
improved on the original; otherwise the regen was noise and the
original wins.

**Pros:**

- Free. No extra LLM call to detect, only the conditional regen call.
  Per-render cost rises 0% when the writer first-drafts within budget,
  ~80% (one extra writer call) when it doesn't.
- Aligned with the user experience. The hover UI Phase 4 ships shows
  the same INFERRED labels the verifier uses; if the user thinks too
  many sentences are tinted, they would have rejected the draft
  anyway.
- Deterministic and inspectable. The threshold is one config knob;
  the formula is one Python function (`_inferred_ratio`). No prompt
  tuning, no judge calibration drift.
- "Improvement only" admission policy. The regen is admitted only
  if its ratio is genuinely lower; otherwise the orchestrator keeps
  the original. Prevents a noisy regen from replacing a slightly-
  worse-tagged but otherwise-stable draft.

**Cons:**

- Gameable in principle. A writer that tags everything `SEED` passes
  trivially. The prompt explicitly tells the writer to be honest, and
  the offline judge catches drift via the per-axis faithfulness
  score in `make eval-live`. For a writer that follows instructions,
  this is good enough; for a future writer that doesn't, ADR-011 may
  need to escalate to the judge-based approach (Option B below).
- The regenerated draft might lose other qualities (warmth, voice)
  that the first draft had. The "improvement only" admission policy
  protects against the worst case (keep original), but a perfectly
  ratio-improved regen could still be stiffer prose. Mitigated by
  the writer prompt's "preserve the chronology and voice" instruction
  in the feedback suffix.

### Option B — Promote the paid judge into production (the original Phase 2.5 brief)

After writer pass returns, run the existing `tests/eval/judge.py` judge
against the ledger and the narrative. If `faithfulness < threshold`,
regenerate with the judge's per-claim verdicts as feedback.

**Pros:** Closes the gameability loophole. The judge applies the same
rubric in production that the eval applies offline.

**Cons:**

- Two LLM calls per render even on the happy path (writer + judge),
  plus conditional regen. ~$0.005 per render added (Sonnet judge),
  vs $0 for Option A.
- Production becomes dependent on the eval layer's prompt and model
  choices. A change to the judge prompt to fix an eval drift would
  silently change production behaviour.
- The judge already runs at eval time; adding a runtime variant
  duplicates the surface area. Option A leverages a signal the
  writer already produces.

### Option C — Defer Phase 2.5 indefinitely

Argue that Phase 4's provenance UI lets the user catch the failure
mode visually; no automated gate needed.

**Pros:** Less surface area. The user sees INFERRED highlights and
can edit / dismiss.

**Cons:** Phase 4.1 (the edit-mode UI) is deferred — for now the user
sees the highlights but can't fix them mid-render. A draft that's 80%
INFERRED ships as-is. Better to let the orchestrator try once to fix
it before the human sees it.

---

## Decision

**Adopt Option A.** After the writer pass produces a validated
`NarrativeOutput`, compute the INFERRED-share via `_inferred_ratio`.
If it exceeds `Settings.max_inferred_ratio` (default `0.5`, env
`MAX_INFERRED_RATIO`), build a feedback suffix and re-call the writer
once. Validate the regen; admit it only if its INFERRED share
improved on the original. The orchestrator's overall failure modes
(retry on bad JSON, error on schema fail) are unchanged; the
verifier sits between the second-JSON-validation and the cache
write.

The verifier is **disabled for streaming**
(`generate_narrative_stream`) — the writer's chunks have already
been pushed to the user by the time the validated narrative arrives;
swapping in a different draft post-stream would be jarring. The web
builder will get a verifier-aware streaming pattern in Phase 4.1
when the edit UI ships.

---

## Consequences

### What changes

- `trailstory/llm/narrative.py`: new `_inferred_ratio()` and
  `_verifier_feedback()` helpers. `generate_narrative` gains a
  `max_inferred_ratio: float | None = 0.5` kwarg and a new
  post-validation regenerate-once block.
- `trailstory/config.py`: new `Settings.max_inferred_ratio` field
  (default `0.5`, env `MAX_INFERRED_RATIO`).
- `trailstory/cli.py`: passes `settings.max_inferred_ratio` through to
  `generate_narrative`.
- `tests/eval/run.py`: pins `max_inferred_ratio=0.5` for goldens so
  the eval reflects production behaviour.
- Tests can pin `max_inferred_ratio=None` to assert single-call
  semantics where the verifier would otherwise interfere.

### What becomes easier

- Phase 4's edit UI inherits the same labels the verifier already
  uses. When the user clicks an INFERRED sentence to edit it, the
  same provenance enum drives both the hover tint and the verifier
  decision.
- A future Phase 2.5 escalation to a judge-based verifier (if Option
  B becomes necessary) only needs to swap `_inferred_ratio` for a
  call to `tests/eval/judge.py`'s judge — the regen / admit logic is
  unchanged.

### What becomes harder

- One more failure mode to reason about: the orchestrator can now
  return a draft different from the first response the writer
  generated. Tests that count `client.complete.call_count` must
  account for the verifier (either disable it via
  `max_inferred_ratio=None` or expect 2 calls).
- The threshold is a guess based on pilot intuition. Real-world
  calibration will need a few weeks of user feedback to know whether
  `0.5` is too strict (regens too often, stiff prose) or too loose
  (passes drafts users would have rejected).

### Known limitations (deliberately not in this PR)

- **Streaming bypassed.** `generate_narrative_stream` does not run
  the verifier. Phase 4.1's edit UI is the structural fix.
- **One regen attempt.** If the regen still exceeds the ceiling, the
  original is returned. We don't loop forever; one regen is the
  trade-off between cost ceiling and quality lift.
- **Cache invalidation.** The narrative cache key still omits
  prompt-version content. A re-run on the same hike with the same
  inputs gets the cached pre-verifier output. Same caveat as ADR-008
  / ADR-009.
