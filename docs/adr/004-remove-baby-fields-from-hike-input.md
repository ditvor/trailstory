# ADR 004 — Remove `baby_name` and `baby_age_months` from `HikeInput`

**Date:** 2026-04
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

`HikeInput` today requires two family-specific fields:

```python
class HikeInput:
    gpx_path: Path
    photos_dir: Path
    seed_text: str
    baby_name: str
    baby_age_months: int
    location_name: str | None
```

These reflect the original product framing — a parent in Munich with a 5-month-old,
generating memories that mention the baby by name. The narrative prompt in
`llm/prompts.py` interpolates both fields directly and instructs the model to weave
them into the story.

Two things have changed since that framing was set:

1. **v0 is shifting to a hosted web service.** The "in bed, on phone, after a hike"
   scenario means a non-technical user filling a form on their phone. Every required
   field that isn't strictly necessary widens the gap between "open the page" and
   "share with grandma." Time-to-share is the v0 north-star metric.

2. **The privacy posture is becoming a product wedge, not a footer.** v0 promises
   "your hike, processed once, never stored." Asking for a child's name and age as
   a required input — even if we delete it after the response — contradicts that
   promise in the user's eyes the moment they read the form. There is no way to
   audit "we deleted it" from the outside; not asking in the first place is
   verifiable.

The seed text already carries any family context the user wants in the story.
Existing eval cases prove this: their `seed_text` fields routinely mention the
baby, the carrier, the parent's emotional state. The model handles it. The flat
`baby_name` / `baby_age_months` fields are doing duplicate work, and they encode
an assumption — that there is a baby — that limits who Trailstory is for.

---

## Options considered

### Option A — Keep the fields, ask for them in the form

Status quo. The web form has two extra inputs.

**Pros:** No schema change. Prompt unchanged.
**Cons:** Two more fields in a form whose entire UX argument is "three fields, fast."
PII required at the door. Excludes hikes that aren't with a baby — couples, solo
trips, friends — from being a natural fit. Privacy story is harder to tell.

### Option B — Keep the fields, make them optional with defaults

`baby_name: str | None = None`, `baby_age_months: int | None = None`. Prompt branches
on presence.

**Pros:** Backwards-compatible. CLI users can keep using the fields if they prefer.
**Cons:** Carries a dead field down through the model, prompt, template, mocks,
eval cases, and goldens for an indefinite period. The product story still says
"family hiking with a baby" because the surface area still mentions it. Optional
PII is still PII surface area; the hosted form would still default to asking
unless we hide them, at which point the fields are not really optional, they're
just unused.

### Option C — Remove the fields entirely (chosen)

Drop both from `HikeInput`. Rewrite the narrative prompt to be subject-agnostic:
the story is about whoever is in the seed text. If the user mentions a child, the
narrative weaves it in. If not, the narrative is about the hike, the people, the
landscape, the effort.

**Pros:**
- Three input fields (GPX, photos, description) match the v0 form exactly.
- No required PII. The privacy claim is literally true: we don't ask.
- Trailstory broadens — parents, couples, solo hikers, friend groups — without
  changing the product positioning.
- The seed text becomes the single source of family context, which is where it
  was always going to land anyway.

**Cons:**
- Existing eval cases mention baby specifics in their `seed_text` *and* their
  flat fields. Removing the flat fields makes the seed text load-bearing in a
  way it wasn't before — narrative quality on hikes where the seed barely
  mentions family will degrade slightly compared to a prompt that was hand-fed
  the baby's name.
- One-time golden refresh required (paid).

---

## Decision

**Use Option C — remove `baby_name` and `baby_age_months` from `HikeInput`.**
The seed text carries any family context. The narrative prompt becomes
subject-agnostic.

This decision is taken together with ADR-005 (the bilingual flat-field refactor)
because they touch the same files; they ship as one PR.

---

## Consequences

### What changes

- `models.py`: `HikeInput` loses `baby_name` and `baby_age_months`.
- `llm/prompts.py`: `SYSTEM_NARRATIVE` and `USER_NARRATIVE_TEMPLATE` are rewritten
  to drop all baby-specific instructions. The previous prompt versions are kept
  as dated comments above the new ones for revertability.
- `llm/narrative.py`: validation contract follows the model.
- `cli.py`: `--baby-name` and `--baby-age-months` options removed.
- `tests/`: every mock-construction site that builds a `HikeInput` is updated.
  Eval case JSON files under `tests/eval/cases/` lose the two fields. Goldens
  are refreshed (paid, in the same PR).
- `templates/memory.html.j2`: any direct reference to baby fields removed.

### What becomes easier

- The web form is exactly three fields, matching the v0 product spec.
- The privacy page can say "we never ask for a child's name" and have it be
  literally true, line-numbered to the model definition.
- Trailstory is usable by anyone hiking, not just parents with infants — the
  audience expands without product work.

### What becomes harder

- A user whose seed text says "we hiked Tegernsee" and nothing else gets a more
  generic narrative than today's prompt would produce when also fed
  `baby_name="Mira", baby_age_months=5`. We accept this: the prompt's job is to
  amplify what the user gives it, not to fabricate intimacy.
- Existing personal goldens (e.g. the user's real hikes generated under the
  current prompt) won't reproduce verbatim against the new prompt. Acceptable
  for v0 since none of those are committed to the repo.

### Follow-up

- After the LLM-judge evaluates 5 generations under the new prompt, check
  `russian_fidelity` and `warmth` scores on cases whose seed text doesn't
  mention family. If `warmth` regresses substantially, revisit with a prompt
  tweak — not by re-adding the fields.
