# ADR 016 — Writer voice tightening + verbatim user phrase anchor

**Date:** 2026-06
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

ADR-015 closed the fabrication problem structurally and added the
measurement layer (banned-substring gates, sentence-length band,
inferred-ratio ceiling) for the voice work it deliberately deferred.
The 2026-06 golden refresh — the first run against the richer ledger —
measured exactly where the voice still drifts:

1. **The travel-essay register survives prompt-level discouragement.**
   Fresh Opus output reproduced the banned phrase "the kind of" in 2 of
   4 cases ("the kind of spring day that hasn't quite made up its
   mind", "the kind of walk that makes you slow your steps on
   purpose") and gate-skirted it in a third ("that good kind of quiet
   wonder", "might be the best kind"). The model reaches for the
   *construction*, not the literal substring — a substring gate alone
   cannot fix that.
2. **Landscape personification persists**: "the day really gave itself
   to us", "the mountain was already letting us go gently", "the whole
   world was wrapped in grey, and we were the only two figures moving
   through it".
3. **The hiker's own words lose to the writer's.** Case 03's seed said
   "my shoulders burned" — the single detail the previous judge run
   explicitly credited for its 4.5 warmth — and the refreshed draft
   dropped it from all three languages, scoring warmth 3.5. Case 01's
   old pull quote was near-verbatim seed ("Just as we reached the
   ridge, the fog lifted."); the refreshed one is a writer image ("a
   soft white sea"). Nothing in the pipeline tethers the output to the
   user's own phrasing, even though the glossary has always said the
   pull quote is "pulled from the seed text or close to it".

The grounding problem was solved by giving the writer better material
(ADR-009/015). The voice problem has the same shape: the writer needs
*examples* of the register, and the output needs a *structural anchor*
to the user's words — not more adjectives in the instruction prose.

---

## Options considered

### Option A — Prompt-only voice rules, no anchor

Add the register positioning and anti-pattern rules to the writer
prompt; rely on the eval to catch drift.

**Pros:** smallest change; no schema or pipeline touch.
**Cons:** the dated SYSTEM_NARRATIVE history in `llm/prompts.py` shows
four rounds of adjective-tuning ("intimate, literary" → "plainspoken" →
"warm and direct") with the drift re-emerging each time; rules without
examples underdetermine the register; nothing stops the writer from
dropping the seed's strongest detail again.

### Option B — Runtime style gates / critic+revise loop

Enforce the voice at generation time: reject drafts containing banned
constructions, or add a critic LLM pass that rewrites them.

**Pros:** hard guarantee.
**Cons:** already evaluated and rejected during the ADR-015 planning
round — a hard gate on style produces stiff prose and retry loops; a
critic pass adds a paid call and a failure point for a 3–5 paragraph
output. The eval is the right place for style enforcement; the
runtime is not.

### Option C — Voice rules + paired examples + verbatim phrase anchor

Three moves in one PR:

1. **Register + rules + examples in the writer prompt.** Position the
   register explicitly ("a warm family note to grandparents — neither
   a travel essay nor minutes of a meeting"), make the anti-patterns
   binding (no personified landscape, no feeling/atmosphere sentence
   subjects, ≤ 2 adjectives per noun phrase, no "the kind of"
   construction family), and show five BAD/GOOD pairs whose BAD halves
   are *real sentences from the refreshed goldens* — not invented
   strawmen — with warm (not clipped) rewrites.
2. **`verbatim_user_phrases` on the ledger.** The extractor pulls 2–4
   short literal phrases (≤ 8 words) from the seed; Python re-verifies
   each as a genuine case-insensitive substring of the seed (an LLM's
   "verbatim" cannot be trusted), dedupes, and caps at four. The
   writer must weave at least one in: verbatim in the language the
   hiker wrote it in, rendered faithfully in the other two. Empty list
   = proceed without.
3. **Free verifier extension (ADR-011 pattern).** Detection is a
   substring scan over all three languages of the draft — no extra
   call. If phrases exist and none surfaced, regenerate once with the
   phrases named; keep the regen only if it actually uses one
   (improvement-only admission). Streaming bypassed, same as ADR-011.

**Pros:** examples carry register where rules can't; the anchor is
structural (the user's words are *in the ledger*, same trust level as
GPX facts); the verifier closes the loop at zero detection cost; all
three moves are measurable by the ADR-015 rubric metrics.
**Cons:** worst case three writer calls per render (base + inferred
regen + verbatim regen); a paraphrasing extractor yields an empty
phrase list and silently loses the anchor (acceptable: documented
proceed-without semantics, and the filter is the price of the verbatim
guarantee).

### Option D — Tone presets

Let the user pick a voice (warm / plain / literary) per render.

**Pros:** sidesteps the "one true register" question.
**Cons:** already evaluated and rejected in the ADR-015 planning
round: the product is opinionated about taste (that *is* the moat —
see the monetization thesis), presets multiply the eval surface by the
preset count, and the grandparent-note register is the product.

---

## Decision

**Option C.** Specifics:

- `SYSTEM_NARRATIVE` and `USER_NARRATIVE_TEMPLATE` gain the register
  positioning, the four binding voice rules, and five BAD/GOOD pairs.
  BAD halves are quoted from the 2026-06 refreshed goldens (cases 01,
  03, 04). Previous prompt versions kept as dated comments per file
  convention.
- `FactLedger.verbatim_user_phrases: list[str]` (default empty) sits
  in the LLM-derived half; `_ExtractorOutput` mirrors it so pre-ADR-016
  extractor responses still validate. `_filter_verbatim_phrases` in
  `llm/narrative.py` enforces the literal-substring guarantee
  (case-insensitive), the ≤ 8-word bound, dedupe, and the cap of 4.
- The extractor prompt gains section 4 + the skeleton key.
- `generate_narrative` gains the verbatim verifier after the ADR-011
  ratio verifier; `VERIFIER_VERBATIM_FEEDBACK_TEMPLATE` lives in
  `llm/prompts.py` (one placeholder: `phrases`). The check scans EN,
  RU, and DE — a hiker who writes the seed in Russian anchors the RU
  text, and only a faithful rendering (which no substring check can
  verify) is asked of the other two languages.
- `NarrativeOutput.schema_version` bumps 4 → 5: the output shape is
  unchanged but cached v4 narratives no longer reflect the current
  writer's register. The version lives in three places that move
  together — the `models.py` default, the JSON skeleton in
  `USER_NARRATIVE_TEMPLATE`, and `web/dev.py`'s fake client — plus the
  test fixtures; `tests/test_prompts.py` pins skeleton == model
  default.

---

## Consequences

**What becomes easier:**

- The register is *shown*, not described — paired examples mined from
  real output are the strongest prompt-side lever left after four
  rounds of adjective tuning.
- The output is tethered to the user's own words: the seed's "my
  shoulders burned" now has a structural path into the prose, not just
  a hope.
- The banned-phrase golden gate (parked again at v4-goldens vs
  v5-model) should arm green after the post-ADR-016 refresh — if "the
  kind of" still survives the new prompt, the gate fails CI and the
  finding is loud.
- The ADR-015 rubric metrics (sentence-length band, inferred ceiling,
  banned substrings) measure exactly the dimensions this ADR moves.

**What becomes harder:**

- Worst case three writer calls per render (base + one ratio regen +
  one verbatim regen), each gated on an actual contract violation.
- The narrative cache invalidates again (v4 → v5), one paid re-run per
  previously-cached hike.
- The verbatim guarantee is only as good as the extractor's recall:
  a paraphrasing extractor produces an empty list and the anchor
  silently disengages. The filter trades recall for precision on
  purpose — a fake "verbatim" quote is worse than none.

**Out of scope (already rejected, do not reintroduce):** storyboard
stage, runtime critic+revise loop, tone presets, hard runtime style
gates. Also out of scope here: feeding the deterministic ledger to the
LLM judge (the judge currently scores seed-only and systematically
under-credits ledger-grounded facts — a known measurement artifact,
candidate for its own ADR).

**Follow-ups:**

- Golden refresh to v5 after this PR's eval run is approved as the
  intended baseline (`make eval-update-golden`) — that refresh re-arms
  the banned-phrase gate.
- Populate `_BANNED_RU` / `_BANNED_DE` from eval observation (the
  2026-06 refresh surfaced candidates: stacked-adjective runs,
  officialese like "внутри помещений", calques like "Art von Staunen").
