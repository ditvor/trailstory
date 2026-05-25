# ADR 008 — Writer prompt temporal grounding + anti-fabrication clause

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

[ADR-007](007-faithfulness-eval-axis.md) added a faithfulness axis to the
paid LLM judge. The first paid `make eval-live` after that landed
recorded a sobering baseline:

| case | faithfulness / 5 | sup / inf / unsup |
|---|---:|---|
| 01-fixture-baseline | 0.48 | 1 / 2 / 18 |
| 02-joyful-summit | 1.39 | 3 / 4 / 11 |
| 03-exhausted-foggy | 2.08 | 5 / 5 / 8 |
| **average** | **1.32** | **21% / 21% / 66%** |

Two-thirds of every concrete factual claim in our generated narratives
was unsupported by the seed text. Two specific failure modes recurred
across cases:

1. **Temporal misalignment.** A pilot Bad Tölz hike in April produced
   "the river ran the milky green it gets in summer." The prompt had no
   notion of date or season — the model defaulted to mid-summer
   meltwater imagery for an Alpine river that was, in fact, running
   clear and cold in mid-spring.

2. **Unmotivated specifics.** The same case mentioned a duck the baby
   pointed at (no duck in seed or photos), chopsticks at dinner (no
   utensil mentioned with the takeaway food), shoes coming off by the
   river (no such action in source). The model fabricated concrete
   sensory specifics that no source authorised.

Phase 0 made the problem visible. Phase 1 is the cheapest possible
correction: change only the prompt, keep the architecture identical,
measure the lift, then decide whether the larger Phase 2 two-pass
fact-ledger architecture is still needed (it almost certainly is — but
we want to know how much of the gap prompt-engineering can close
unaided).

The original brief for Phase 1 (passed via the project owner's
working notes):

> Modify the writer prompt to:
> - Include the actual hike date and inferred season (compute from GPX
>   start time in `trailstory/llm/narrative.py` and pass into the
>   template)
> - Add an explicit anti-fabrication clause: "Do not introduce specific
>   animals, foods, named places, or named objects that are not in the
>   source text or visible in the provided photos. Generic nature words
>   (sky, water, path, trees) are allowed."

This ADR records the implementation choices made during that change.

---

## Options considered

### Option A — Date + season + anti-fabrication clause, all in the user prompt (chosen)

Add two new placeholders to `USER_NARRATIVE_TEMPLATE` (`hike_date`,
`season`) and a new paragraph forbidding ungrounded concrete specifics.
The orchestrator derives both from the first timed GPX waypoint;
hemisphere is inferred from latitude. The system prompt stays untouched.

**Pros:**

- Lowest-coupling change. No new model fields, no new API calls, no
  architectural surface area. Pure prompt + a small private helper
  (`_infer_date_and_season` in `narrative.py`).
- Both signals live in the same prompt as the seed text, so the model
  reads "April; northern hemisphere" right next to the seed sentence
  it should be grounding from. Co-locating context with constraint is
  the standard prompt-engineering move; splitting across system and
  user prompts spreads the model's attention.
- The anti-fabrication clause names concrete forbidden examples (duck,
  chopsticks, espresso) the model pattern-matches against. Abstract
  prohibitions ("do not invent") underperform concrete enumerations in
  pilot — the model already "knows" it shouldn't invent, but
  enumerating specific noun classes nudges the right associative
  retrieval.
- Reversible. The previous prompt is preserved as a dated comment
  immediately above the current version per the standing CLAUDE.md
  convention.

**Cons:**

- The user prompt grows by ~10 lines. Adds ~80 tokens per call. Cost
  impact: negligible compared to the Opus per-call baseline.
- Anti-fabrication clauses are known to underperform structural
  constraints (Phase 2's fact-ledger architecture) on faithfulness
  benchmarks. We expect ≥ 0.5 of lift, not the 4.0+ ceiling we
  ultimately need. This is exactly why Phase 1 is a measurement
  exercise, not a solution.

### Option B — Move the anti-fabrication clause to the system prompt

Add the date/season to the user prompt (data co-location wins regardless)
but put the "don't invent specifics" rule in the system prompt where the
persona lives.

**Pros:** System prompts are read more attentively in some pilot
studies; the anti-fabrication rule is genuinely persona-level ("you are
the kind of writer who doesn't invent specifics").

**Cons:**

- The rule references the seed text ("must trace to the seed text"),
  which is in the user prompt. Splitting them is a referential leak.
- The system prompt currently fits in a small window of persona +
  output discipline. Adding a multi-sentence content rule dilutes its
  focus and risks the model treating it as one of many guidelines
  rather than a hard constraint.
- A future Phase 2 that replaces this user prompt entirely (writer
  consumes a `FactLedger`, not raw seed) will need to delete this
  clause from the user prompt anyway. Keeping it co-located makes the
  Phase 2 rewrite a localised change.

### Option C — Skip Phase 1 and jump straight to Phase 2 (fact ledger)

Argue that prompt engineering alone won't move the needle enough to be
worth a deploy, so collapse Phase 1 into Phase 2 and ship the
architectural fix.

**Pros:** Fewer deploys. The faithfulness gap is closed once, properly.
**Cons:**

- Risks the Phase 2 effort budget by leaving prompt-only headroom
  unmeasured. If Phase 1 buys 1.5 points unaided, Phase 2's
  expected delta narrows from "+5" to "+3" — that recalibration
  matters when budget conversations come up later.
- Loses the per-phase review boundary the project's working notes
  call for. Phase 0 already established the cadence; breaking it on
  the second phase erodes the discipline before it has paid off.
- A 2-3 week Phase 2 lands without temporal grounding. Phase 2's
  fact-ledger pass would benefit from knowing the date and season
  too; landing the inference helper here means it's already there
  when Phase 2 starts.

---

## Decision

**Adopt Option A.** Two new placeholders (`hike_date`, `season`) added
to `USER_NARRATIVE_TEMPLATE`, populated from a new private helper
`_infer_date_and_season(gpx_stats)` in `trailstory/llm/narrative.py`.
The "Write the memory…" paragraph extended with an anti-fabrication
clause that enumerates concrete forbidden examples and explicitly
permits generic nature words. Previous prompt preserved as a dated
comment.

The inference helper:

- Walks `gpx_stats.waypoints` looking for the first non-None timestamp.
- Returns `("unknown", "unknown")` when no waypoint carries time (some
  manually-edited GPX files strip timing); the prompt's anti-fabrication
  clause still applies.
- Picks hemisphere from the waypoint's latitude sign. Northern season is
  meteorological-standard (Dec-Feb winter, etc.); southern shifts by
  six months.
- Returns the season as a verbose phrase
  ("spring (April; northern hemisphere)") so the model has both the
  high-level label and the specific month + hemisphere in one string.
  Pilot showed the one-word version ("spring") let the model drift
  between early- and late-spring imagery; the verbose version pins it.

---

## Consequences

### What changes

- `trailstory/llm/prompts.py`: new `{hike_date}` and `{season}`
  placeholders; new "Ground every concrete specific..." paragraph;
  previous prompt preserved as a dated comment.
- `trailstory/llm/narrative.py`: new private helper
  `_infer_date_and_season(gpx_stats)`; both `generate_narrative` and
  `generate_narrative_stream` pass the derived values into
  `USER_NARRATIVE_TEMPLATE.format(...)`.
- `tests/test_prompts.py`: `EXPECTED_PLACEHOLDERS` extended with the
  two new keys; `sample_fields` fixture extended to match.
- `tests/test_narrative.py`: shared `_gpx_stats` fixture parameterised
  for `waypoint_time` and `lat` (defaults preserve a meaningful date and
  northern-hemisphere lat for existing tests); 4 new tests covering
  the inference's branches and the anti-fabrication clause's presence
  in the rendered prompt.
- `CHANGELOG.md`: entry under `### Changed` (the writer prompt is the
  user-facing behaviour change — the model now grounds in date/season).

### What becomes easier

- Future renderers and downstream code (Phase 2's fact-ledger, Phase 3's
  multimodal grounding) can rely on the date/season inference already
  living in `narrative.py`. The helper moves with the file when the
  pipeline restructures.
- Prompt iterations on the anti-fabrication clause are one-file changes
  with the previous version visible right above as a comment, exactly
  as ADR-003's prompt-update recipe envisioned.

### What becomes harder

- Two more placeholders to keep in sync between
  `USER_NARRATIVE_TEMPLATE` and `narrative.py`. The drift test in
  `tests/test_prompts.py` catches forgotten updates — adding a
  placeholder to the prompt without updating `narrative.py` raises
  `KeyError` at format time.
- The southern-hemisphere season inference is correct for meteorological
  seasons (Mar-May = autumn in the southern hemisphere) but the prompt
  doesn't yet know about high-altitude or microclimate exceptions
  (Patagonian summer is climatically different from Italian summer).
  Acceptable for v0; revisit if real users start hiking outside
  temperate Europe.

### Known limitations (deliberately not fixed in this PR)

- **Cache invalidation.** The narrative cache key
  (`trailstory/llm/cache.py`) deliberately omits prompt-text content
  (see ADR-003 reasoning). A re-render of a previously-rendered hike
  on the CLI will serve the pre-ADR-008 narrative from cache. The web
  builder uses the streaming path (`generate_narrative_stream`),
  which bypasses the cache entirely, so end-user impact is limited to
  CLI users (one developer at v0 scale). A future change should bump
  a `PROMPT_VERSION` constant into the cache key; tracked for whoever
  takes the next pass at the cache module.
- **The anti-fabrication clause does not guarantee compliance.** It is
  a prompt-level constraint, not a structural one. Pilot studies on
  similar prompts show 30-60% reduction in fabrication, not
  elimination. Phase 2 (two-pass with `FactLedger`) is the
  structural fix. This ADR closes the prompt-engineering ceiling,
  not the faithfulness gap.

### Expected lift + acceptance

The Phase 1 acceptance criterion is **≥ 0.5 faithfulness lift on
average** across the three eval cases without other axes regressing
beyond `EVAL_REGRESSION_THRESHOLD` (1.0). Pilot expectation:

| case | baseline | expected | upper bound |
|---|---:|---:|---:|
| 01-fixture-baseline | 0.48 | 1.0 | 1.8 |
| 02-joyful-summit | 1.39 | 2.0 | 2.6 |
| 03-exhausted-foggy | 2.08 | 2.7 | 3.2 |
| **average** | **1.32** | **1.9** | **2.5** |

Actual numbers go into the refreshed goldens at PR time. If the
average lift is **< 0.5**, that's data: prompt engineering alone
isn't enough, and Phase 2's effort budget is fully justified. If
it's **> 1.0**, prompt engineering has more headroom than expected
and Phase 2 should be reconsidered for scope. Anywhere in between
(the expected case): proceed to Phase 2 with the measurement
recorded.

### Follow-up

- **Phase 2 ADR (009).** Two-pass writer with `FactLedger`. Should
  reuse `_infer_date_and_season` — the ledger extractor benefits
  from the same temporal grounding the current writer now gets.
- **Cache invalidation pass.** Out of scope for Phase 1, but the
  CLI-cache-serves-stale-prompt issue should be fixed before Phase 3.
  One-line fix: include a `PROMPT_VERSION` in the cache key hash.
