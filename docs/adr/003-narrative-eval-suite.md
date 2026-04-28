# ADR 003 — Narrative-quality eval is a programmatic rubric, with a paid LLM-judge layer added separately

**Date:** 2026-04
**Status:** Accepted
**Decided by:** initial product owner

---

## Context

The narrative is the single user-facing creative output of Trailstory and
costs an Opus call per hike. We have no automated way to detect that a
prompt change has regressed quality — today the only signal is reading
the rendered HTML by hand. That is expensive, slow, and easy to skip
under deadline pressure, which means the project either accumulates
silent quality regressions or freezes prompt iteration entirely.

We need a regression gate that:

1. Catches structural failures (wrong field types, off-by-one paragraph
   counts, indices outside `[0, n_photos)`) before they reach the renderer.
2. Catches the bilingual failure modes that matter for the grandparents
   in Russia: "the model dropped into English mid-paragraph", "the
   Russian translation is half the length of the English", "the title is
   60 characters of advertising-copy filler".
3. Is cheap enough to run on every prompt iteration, including in
   `make ci`.
4. Has a believable human story for taste-level quality
   ("does this read well", "is the literary register intact") even
   though we do not have that today.

Two design questions were open:

- **One gate or two?** A single fused gate would be simpler. Splitting
  lets the always-on layer stay cheap and deterministic while a paid
  layer handles judgment.
- **What goes in the rubric?** Anything programmatic and unambiguous; or
  also LLM-judge-as-rubric items.

---

## Options considered

### Option A — Programmatic rubric only, paid LLM judge later as a separate gate (chosen)

Two layers, shipped in two PRs:

| Layer | This PR (`feat/eval-rubric`) | Future PR (`feat/eval-judge`) |
|---|---|---|
| What | Pure-Python checks: schema round-trip, paragraph count, Cyrillic coverage + ASCII-run guard, EN/RU word ratio, length caps, photo-index validity, pull-quote provenance | LLM-as-judge calls scoring tone, literary register, translation quality, narrative arc |
| Cost | Free | One paid call per case per run |
| Where it runs | `make ci` (unit tests of rubric) + `make eval` (rubric against real LLM output) | `make eval-judge` only |
| Determinism | Deterministic | Sampled — needs threshold + retry policy |
| Catches | Structural and gross translation failures | Taste, register, voice |

**Pros:**
- Always-on gate is free and deterministic — runs on every prompt
  iteration without billing anxiety.
- The split keeps the cheap layer's contract simple: every check is one
  function returning `RubricResult(name, passed, detail)`.
- Adding the judge layer later does not require restructuring this one.
- Unit tests (`tests/test_eval_rubric.py`) exercise the rubric on
  hand-built fixtures with no LLM call, so the gate's own logic can be
  refactored safely.

**Cons:**
- The rubric is only as good as its checks. A model could pass every
  check and still produce flat, uninspired prose. That gap is what the
  judge layer will close.
- Two suites to maintain instead of one.

### Option B — Single fused gate that mixes deterministic and LLM-judge checks

One `make eval` target, every check uniform.

**Pros:** Simpler mental model. One number per case.
**Cons:** Every CI run becomes paid (or every CI run skips half the
checks). The judge layer's nondeterminism leaks into the deterministic
checks' threshold tuning. Mocking for the unit tests becomes much harder.

### Option C — No rubric; rely on hand review until a real problem appears

Cheapest, fastest, and what most personal projects do.

**Pros:** Zero engineering cost today.
**Cons:** The whole point of building Trailstory carefully is that the
parents who read these are family, not us — quality regressions hurt
people who cannot file a bug. We have already shipped one privacy
regression caught only by review (GPS-EXIF leak, ADR-002-adjacent); we
should not bet the user-facing creative output on the same review
process.

---

## Decision

**Ship the programmatic rubric as the always-on gate. Defer the paid
LLM-judge layer to a separate PR (`feat/eval-judge`) so it lands with
its own design and cost tradeoffs explicit.**

The rubric is mechanical. Each check is one function in
`tests/eval/rubric.py`, returns a `RubricResult(name, passed, detail)`,
and is exercised by `tests/test_eval_rubric.py` on hand-built fixtures
that never call the API. A real-API runner at `tests/eval/run.py` (one
case at a time or all cases) calls `generate_narrative` with a real
`AnthropicClient`, applies the rubric, prints a per-case table, and
exits non-zero on any failure.

Cache is **always disabled** for the eval runner. The cache key
deliberately does not include prompt text (see `llm/cache.py`), so a
cache hit from a previous prompt version would defeat the eval. Every
run is a fresh paid call.

The CLAUDE.md "Update a prompt" recipe is rewritten to put `make eval`
between the prompt edit and the merge. A future change to the default
that lands a prompt regression is therefore visible at PR time.

---

## Consequences

### What changes

- A new `tests/eval/` package with the runner, the rubric, three
  fixture cases (`01-fixture-baseline.json`, `02-joyful-summit.json`,
  `03-exhausted-foggy.json`), and an empty `golden/` directory
  populated by `python -m tests.eval.run --update-golden`.
- A new `make eval` target documented as **paid**.
- A new `tests/test_eval_rubric.py` exercising every rubric function
  with positive and negative fixtures. Runs in `make ci`. Free.
- The CLAUDE.md prompt-update recipe explicitly requires running the
  rubric and inspecting the table.

### What becomes easier

- Iterating on `llm/prompts.py` without manual inspection of every case.
- Catching translation regressions ("paragraph 2 stayed in English") at
  PR time rather than after a parent re-shares the page.
- Onboarding: a contributor changing a prompt has a one-line command
  and a clear gate.

### What becomes harder

- Every prompt iteration costs roughly `n_cases × per-call cost`. With
  three cases on Opus that is on the order of a dollar per cycle.
  Acceptable at personal-use volume.
- The rubric thresholds (paragraph count 3-5, EN/RU ratio 0.7-1.4,
  pull-quote overlap ≥ 0.60, etc.) are calibrated against what a good
  Opus narrative looks like today. If the prompt evolves to produce
  legitimately different shapes, the rubric must move with it. That is
  on purpose: the rubric edit lives in the same PR as the prompt change,
  with reasoning visible in the diff.

### Follow-up

- `feat/eval-judge`: paid LLM-as-judge layer scoring taste-level
  quality. Lives in its own runner under `tests/eval/judge.py` (or
  similar) and its own Makefile target. Out of scope for this PR.
- After ~10 real hikes generated under the rubric, revisit thresholds:
  any check that has fired only on regressions stays; any check that
  has fired on legitimately good output gets relaxed.
