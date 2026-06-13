# ADR 018 — Enriched photo description (scene / light / legible text / orientation-free interactions)

**Date:** 2026-06
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

A real-world render produced a faithfulness bug: the seed said the baby
was "in a carrier"; the photos showed it clearly; the narrative asserted
the baby was in a carrier **on the back** — a detail no source supports.
Tracing the pipeline (ADR-009 two-pass writer, ADR-010 photo grounding)
showed the cause was structural, not a one-off:

1. The `PhotoDescription` the vision pass produces had **five** fields
   (`people_visible`, `objects_visible`, `location_clues`,
   `season_clues`, `body_language_notes`). None could represent carry
   orientation, and the describer prompt never asked for it. So "on the
   back" could not have been grounded from a photo — it was a writer
   elaboration of the grounded-but-vague noun "carrier".
2. The anti-fabrication clause (ADR-008) gates ungrounded **nouns**, not
   **modifiers on a grounded noun**. "carrier" is legitimate; "on the
   back" rode in on it. The verifier loop (ADR-011/016) checks only
   self-reported provenance tags and verbatim-substring presence —
   neither inspects spatial claims.

Two questions followed: can we **enrich** the photo output so the writer
has better, grounded context (and so orientation is grounded when it is
genuinely visible)? And does enrichment help or simply widen the
fabrication surface?

## The spike

We prototyped an enriched describer (adding `interactions`,
`legible_text`, `scene_type`, `light_and_color`, and a `certainty` flag)
and ran it on the six **real** photos of eval case `04-bad-tolz-family`,
A/B-ing `claude-haiku-4-5` against `claude-sonnet-4-6`, with the actual
images as ground truth. Findings:

- **The bug reproduced live.** On the one genuinely ambiguous carrier
  photo, despite an explicit *"NEVER guess orientation"* instruction:
  Sonnet asserted "child carrier **on the chest**" (wrong), and Haiku
  hallucinated "holding **a dog**" (there is no dog). Every
  orientation-free phrase ("in their arms", "leaning over", "wearing a
  child carrier"), by contrast, was accurate. **Conclusion: the carry
  *fact* is reliable; the *orientation* is exactly the part that
  fabricates, and instruction alone does not suppress it.**
- **The other new fields are safe and useful.** `scene_type` and
  `light_and_color` were accurate on both models; `legible_text`
  returned empty (correctly — no signage in these photos) with no
  hallucination.
- **Sonnet reads fine detail better.** It identified the "child carrier"
  where Haiku saw a "dog" and the baseline saw a "backpack" — which is
  precisely the detail the enriched fields lean on.
- **A single `certainty` flag was too coarse** — both models said
  "clear" even for the ambiguous carrier. Dropped.

## Decision

Enrich `PhotoDescription` (amending ADR-010's shape), with `interactions`
deliberately constrained:

1. **New fields:** `interactions: list[str]`, `legible_text: list[str]`,
   `scene_type: str | None`, `light_and_color: str | None`. Existing
   five fields unchanged. No `certainty` field.
2. **`interactions` is orientation-free by contract.** The describer
   prompt forbids front/back/chest/hip; and a Python scrubber
   (`photos._scrub_orientation`, applied to `interactions` and
   `body_language_notes` in `describe_photo`) strips any orientation
   modifier that slips through while **keeping** the grounded carry fact
   ("wearing a child carrier on the back" → "wearing a child carrier").
   This mirrors the rubric's banned-substring approach and is
   defense-in-depth behind the prompt.
3. **Vision model default → `claude-sonnet-4-6`** (`Settings.vision_model`),
   overridable via `VISION_MODEL`. The enriched fields need the
   fine-detail reliability the spike showed Haiku lacks. Drop back to
   Haiku for cost-sensitive batch runs.
4. **Writer-side rule.** A new hard rule in the writer prompt forbids
   asserting the position/orientation of a carried person or worn object
   unless the ledger states it. Enrichment grounds orientation *when
   visible*; this rule stops invention *when it is not*. Both halves are
   required.
5. **Ledger extractor** is told to fold `interactions` into a person's
   `role` or a beat's `activity`, treat `legible_text` place/route names
   as corroborating `where`, and **never re-introduce** an orientation
   the description does not state.

The downstream writer prompt is otherwise unchanged: photos still enrich
the ledger, they do not bypass the ADR-009 fabrication contract.
`NarrativeOutput.schema_version` is unaffected — `PhotoDescription` is
not part of `NarrativeOutput`.

## Consequences

- The vision cache (ADR-012) is keyed by `(photo bytes, vision_model)`;
  the model default change invalidates existing Haiku-keyed entries
  cleanly (they will be re-described with Sonnet). Cached entries missing
  the new fields validate to defaults, so old same-model entries do not
  break.
- Per-photo vision cost rises (Haiku → Sonnet). Bounded task, small
  absolute cost, overridable.
- **Measurement gap surfaced:** three of four eval cases point at
  solid-colour placeholder fixtures that return empty descriptions, so
  the suite barely exercises photo grounding. Only `04-bad-tolz-family`
  uses real photos. Adding real-photo eval cases — including one with a
  legible summit marker to exercise `legible_text` — is follow-up work;
  it needs photo assets not yet in the repo.
- Goldens: the writer-prompt rule can shift narrative output; refresh
  with `make eval` / `make eval-update-golden` per the "Tune a prompt"
  recipe and report the before/after tables in the PR.

## Rejected alternatives

- **`interactions` with orientation, gated only by instruction** — the
  spike showed even Sonnet ignores the instruction. Rejected; the
  orientation ban + scrubber is the structural fix.
- **A `certainty` flag to down-weight uncertain observations** — too
  coarse in the spike (always "clear", including for the ambiguous
  carrier). Rejected.
- **A verifier that flags spatial claims** — cannot detect "on the back"
  as ungrounded without comparing to the ledger, and keyword detection
  is brittle ("on the way back"). Not pursued.
- **Keeping Haiku for vision** — its "dog" hallucination on the carrier
  photo is disqualifying for fields that depend on fine detail.
