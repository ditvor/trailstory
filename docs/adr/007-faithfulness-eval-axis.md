# ADR 007 — Faithfulness eval axis

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

A real generated narrative for a hike at Bad Tölz (April 2026, four people
and a baby) introduced concrete details that did not appear in the seed
text, the photos, or the GPX: a duck the baby pointed at, the river running
"the milky green it gets in summer" (the hike was mid-spring, not summer),
a gasp from one of the adults, shoes being taken off by the water. Manual
audit of one case put the share of invented concrete details at roughly
**40%** of the narrative's specific sensory beats.

The existing judge ([ADR-003](003-narrative-eval-suite.md)) scores four
taste-level axes — `warmth`, `narrative_arc`, `russian_fidelity`,
`photo_selection_plausibility`. None of them penalise fabrication. A
narrative that invents a duck the writer never saw can score 5/5 on every
axis because the prose is well-formed, the arc is satisfying, and the
Russian translation is faithful to the (invented) English. The product
promise — a memory page family abroad can trust — collides head-on with
that gap.

Trailstory is a **memory** product, not a creative-writing product. The
recipients are family who weren't there; they read the page as
biographical reportage, not as a short story "inspired by" the hike. The
single biggest investment lever left for v1 narrative quality is closing
the fabrication gap.

This ADR opens the first of five phases toward that goal. The full plan
is laid out in the project owner's working notes; subsequent ADRs (008
onward) will cover the structural changes (two-pass with fact ledger,
multimodal photo grounding, sentence-level provenance). This ADR is
deliberately the smallest possible move: **measure the problem before
fixing it.** Without a faithfulness score in the eval suite, every prompt
or architecture change downstream is unfalsifiable.

---

## Options considered

### Option A — Add a `faithfulness` axis to the judge, derived from per-claim verdicts (chosen)

The judge is asked to walk the English narrative once, extract every
concrete factual claim (named people, named objects, specific actions,
weather/season/sensory details), and label each one
`SUPPORTED` / `INFERRED` / `UNSUPPORTED` with a source quote.

Python derives the score from the verdicts:

```
faithfulness = (Σ weight(verdict)) / n_claims · 5
weight(SUPPORTED) = 1.0
weight(INFERRED) = 0.5
weight(UNSUPPORTED) = 0.0
```

The score is a `@computed_field` on `JudgeScore`; the LLM never sees it
in the prompt and cannot write it in its response.

**Pros:**

- One axis, same 0-5 scale, slots into the existing `JUDGE_AXES` tuple
  and the existing regression gate (`EVAL_REGRESSION_THRESHOLD`) with no
  bespoke plumbing.
- Per-claim verdicts are persisted in the golden file. A future low
  score is auditable: the human reviewer sees exactly which claims
  failed and why, not just a number. The claim list also functions as a
  longitudinal log — if the same duck-class invention keeps reappearing
  across prompt iterations, the golden makes the pattern visible.
- Arithmetic is in Python. The judge labels claims; Python adds them up.
  LLMs are unreliable at arithmetic, and "the judge gave faithfulness =
  5/5 but its own verdict list contains 3 UNSUPPORTED claims" would be a
  silent contradiction.
- Reversible. The axis can be removed by deleting one tuple entry and one
  computed property; no downstream consumers depend on it (it's eval-only).

**Cons:**

- The judge now does more work per call: ~10-25 verdict objects per
  case, on top of the four taste-level axes. In pilot, this roughly
  doubles the judge prompt's output token count. The cost increase is
  ~0.5¢ per case at Sonnet rates — still negligible compared to the
  writer's Opus call.
- The judge is sampled, not deterministic. Two runs on the same
  narrative may extract slightly different claim lists. The existing
  `EVAL_REGRESSION_THRESHOLD` (1.0 on the 0-5 scale) absorbs that noise;
  pilot variance was within ±0.4.
- The judge's own taste for "what counts as a concrete claim" is not
  fully pinned down by the prompt. Two judges with the same rubric
  might extract 15 vs 22 claims from the same narrative, biasing the
  score. Mitigation: the prompt explicitly enumerates the four claim
  kinds (named people, named objects/places/food, specific actions,
  weather/season/sensory details) and rules out subjective prose and
  metaphor.

### Option B — Have the judge output a single `faithfulness` float directly, like the other axes

Add `faithfulness: float = Field(ge=0, le=5)` next to `warmth`,
let the rubric paragraph define the scale, no per-claim breakdown.

**Pros:** Simplest possible change. No new model types, no Python
arithmetic, no golden-file growth.

**Cons:**

- Not auditable. When the score drops from 4.2 to 2.8 between two prompt
  iterations, the only signal is the `notes` field — and the judge has
  to fit four other axes' justifications into the same 2-4 sentences.
  In practice the notes degrade to "the narrative had fabrications" with
  no specifics, which is exactly the diagnostic gap that motivates this
  ADR.
- No way to distinguish "the judge thinks the narrative is unfaithful"
  from "the judge had a bad day." Per-claim verdicts function as a
  per-call audit trail; a single float does not.
- The scoring formula is implicit in the rubric prompt. Future tuning
  ("weight INFERRED at 0.3 instead of 0.5") requires re-prompting the
  judge instead of changing one constant in Python.

### Option C — Standalone faithfulness pass, separate from the existing judge

A new module `tests/eval/faithfulness.py` with its own model and prompt,
called from `tests/eval/run.py` alongside the existing judge.

**Pros:** Cleanly decoupled — faithfulness changes don't risk regressing
the existing taste-level axes.

**Cons:**

- Doubles the per-case API cost (two judge calls per case instead of one).
- Two regression gates instead of one; the runner table grows a separate
  section; the threshold logic needs to handle two score sources.
- Goldens fragment: a separate `*-faithfulness.json` per case alongside
  `*-judge.json`. Refresh logic needs another branch.
- The taste axes and faithfulness already share a context (the seed text,
  the narrative JSON, the same judge persona). Splitting them duplicates
  prompt context without producing more honest scores.

---

## Decision

**Adopt Option A.** Add `faithfulness` as a derived `@computed_field` on
`JudgeScore`, fed by a new `claim_verdicts: list[ClaimVerdict]` field
that the judge populates. The judge prompt acquires one new rubric
paragraph explaining the four claim kinds and the three verdict labels;
the JSON skeleton acquires `claim_verdicts`. The runner's `JUDGE_AXES`
tuple acquires `"faithfulness"` so the score appears in the per-case
table and is gated by the existing `EVAL_REGRESSION_THRESHOLD`.

The score formula is in Python, not in the prompt, and is owned by
`_VERDICT_WEIGHTS` in `tests/eval/judge.py`. Tuning the weights is a
one-line change with no prompt re-issue.

---

## Consequences

### What changes

- New types in `tests/eval/judge.py`: `FaithfulnessVerdict` (StrEnum) and
  `ClaimVerdict` (frozen Pydantic model).
- New fields on `JudgeScore`: `claim_verdicts: list[ClaimVerdict]` (model
  field, default empty) and `faithfulness: float` (computed_field, 0-5).
- One new rubric paragraph in `USER_JUDGE_TEMPLATE`, one new key in the
  embedded JSON skeleton (`claim_verdicts`), the previous prompt version
  kept as a dated comment for revertability.
- `JUDGE_AXES` in `tests/eval/run.py` extended to include
  `"faithfulness"`.
- 10 new unit tests in `tests/test_eval_judge.py` covering the derived
  arithmetic, the enum/model validation, the model_dump round trip,
  legacy-golden compatibility, and the prompt-wiring contract.

### What becomes easier

- A future prompt or architecture change (Phase 1: prompt-only fixes;
  Phase 2: two-pass with fact ledger; Phase 3: multimodal grounding) can
  be evaluated against the same eval set with one number plus an
  auditable per-claim breakdown. The argument for the bigger investment
  in Phase 2 is now numeric, not anecdotal.
- A reviewer reading a golden file (or a PR diff of refreshed goldens)
  sees the actual claims and verdicts. Reviewing "why did this golden
  refresh raise faithfulness from 4.1 to 4.6" becomes
  reading a diff of the verdict list, not re-reading the entire narrative.

### What becomes harder

- The judge's per-call output is larger. Sonnet handles it comfortably
  today, but if claim counts balloon past ~50 per case the prompt may
  need a per-axis split (Option C, deferred).
- The judge's claim-extraction taste is now part of the eval contract.
  If the judge model changes (`EVAL_JUDGE_MODEL` is configurable), the
  baseline shifts. Documented as a known sensitivity; the project
  doesn't have a budget for cross-model judge calibration in v0.

### Backwards compatibility

Existing pre-faithfulness goldens have no `claim_verdicts` key. The
default `Field(default_factory=list)` lets them validate cleanly;
`faithfulness` then computes to `0.0`. The first `make eval-live` after
this lands will print a faithfulness column with `Δ` showing the gap to
zero — a meaningful positive number on first run, not a regression. The
`make eval-update-golden` refresh then persists real verdicts; subsequent
runs gate against those.

The `tests/test_eval_judge_prompts.py` drift tests continue to enforce
that every `JudgeScore.model_fields` entry appears in the prompt and the
JSON skeleton. `faithfulness` is a computed_field, not a model_field,
and is intentionally absent from both — the LLM does not write it.

### Follow-up

- **Baseline scores.** The first `make eval-live` after this ADR lands
  will record per-case baselines in the refreshed
  `tests/eval/golden/<case>-judge.json` files. Expectation from the
  hand-audit: 5-6 / 10 (i.e. 2.5-3.0 on the 0-5 scale) on the existing
  three cases. Below 2.5 would suggest the rubric is too strict; above
  3.5 would suggest the existing prompt is already better than the
  pilot manual audit indicated, and Phase 2's effort budget needs
  re-justifying. Either way: the number lives in the goldens, not in
  the ADR, because the goldens move and the ADR doesn't.
- **Phase 1.** The next ADR (008) covers the prompt-only fixes — pass
  the GPX-derived date and inferred season into the writer prompt, add
  an explicit anti-fabrication clause. Acceptance: faithfulness moves
  up by ≥ 0.5 on average without regressing other axes.
- **Threshold review.** If pilot noise on faithfulness exceeds 0.5
  across re-runs, the regression threshold may need a per-axis override
  (currently single global value). Defer until we have ≥ 10 real runs.
