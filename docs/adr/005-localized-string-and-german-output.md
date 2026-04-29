# ADR 005 — `LocalizedString { en, ru, de }` and German narrative output

**Date:** 2026-04
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

`NarrativeOutput` today carries every user-facing string twice: once in English,
once in Russian. The shape is flat:

```python
class NarrativeOutput:
    title_en: str
    title_ru: str
    subtitle_en: str
    subtitle_ru: str
    paragraphs_en: list[str]
    paragraphs_ru: list[str]
    pull_quote_en: str
    pull_quote_ru: str
    milestone_en: str
    milestone_ru: str
    selected_photo_indices: list[int]
```

This works for two languages. v0 is adding a third — German — for two reasons:

1. **Geography.** The user lives in Munich. Local recipients (neighbours,
   colleagues, in-laws) read German more comfortably than English. Sharing a
   memory in German is a real use case the moment the tool exists.
2. **Audience completeness.** With EN, RU, and DE the three primary audiences
   for a typical Munich-based hiking memory are covered. Adding DE without
   adding e.g. French is not arbitrary — it matches the user's actual social
   graph.

The flat-field shape doesn't scale gracefully. Adding DE under the current
pattern produces fifteen fields (five strings × three languages) plus a
`paragraphs_de`. The `templates/memory.html.j2` language toggle, which today
flips a CSS class to hide one set of `_en`/`_ru` blocks, becomes a three-way
switch where every block must declare its language. Mocks, eval cases, and
goldens all carry the linear field-count cost.

The CLAUDE.md guidance for adding a third language already says: *"add it to
the model and template in a single PR. Do not add partial language support."*
It does not specify the model shape. This ADR pins the shape down before the
work starts.

---

## Options considered

### Option A — Add flat `_de` fields (`title_de`, `subtitle_de`, ...)

Symmetric with the existing pattern. One PR, mechanical.

**Pros:** Minimal disruption; existing code reads the same way; current mocks
extend by N lines; Jinja access patterns (`narrative.title_en`) already
reference fields directly.
**Cons:** The shape gets uglier with each language. A future Italian / French /
Spanish pass would multiply field count again. The Pydantic schema sent to
the model in the prompt also doubles in size, which makes the prompt longer
to read and harder to keep tidy. Doesn't address the *underlying* design
issue — it just defers it one language at a time.

### Option B — `LocalizedString { en, ru, de }` model (chosen)

Introduce a `LocalizedString` Pydantic model. Each user-facing field becomes
one `LocalizedString` instead of two flat strings. Paragraphs use a small
sibling type (e.g. `LocalizedParagraphs { en: list[str]; ru: list[str]; de: list[str] }`)
because a flat `LocalizedString` cannot carry list values.

```python
class LocalizedString(BaseModel):
    en: str
    ru: str
    de: str

class NarrativeOutput(BaseModel):
    title: LocalizedString
    subtitle: LocalizedString
    paragraphs: LocalizedParagraphs
    pull_quote: LocalizedString
    milestone: LocalizedString
    selected_photo_indices: list[int]
```

**Pros:**
- The shape is the same regardless of language count. Adding a fourth language
  later is one field added to two Pydantic models, not five new fields plus a
  template overhaul.
- Template access becomes `{{ narrative.title.en }}`, `.ru`, `.de` — a clean
  three-way switch with one repeated structure rather than three parallel
  blocks per field.
- Prompt JSON skeleton becomes shorter and cleaner because the structure is
  hierarchical rather than flat.
- Mocks construct one `LocalizedString({"en": ..., "ru": ..., "de": ...})` per
  field instead of three lines.

**Cons:**
- Refactor cost: every site that touches `NarrativeOutput` field access
  changes (the renderer, the template, the rubric, the judge prompt skeleton,
  every mock, every golden file).
- One paid golden refresh.
- The runtime shape sent over the wire to the model is slightly more nested,
  which the model handles fine but is mildly more verbose to instruct.

### Option C — EN + RU now; DE as a separate post-translation pass

Generate EN + RU in one call as today; run a second LLM call to translate to
DE.

**Pros:** No model shape change today.
**Cons:** Two API calls per memory — doubles the cost contribution of the
narrative call relative to one tri-lingual generation. Translation drift
(post-hoc translation tends to be flatter than direct generation in the
target language). Defers the structural problem and re-introduces it the
moment a fourth language is requested.

---

## Decision

**Use Option B — refactor to `LocalizedString` and add German to the same
single LLM call** that today produces EN + RU. The narrative prompt is rewritten
to instruct the model to produce all three languages in one structured response.

This decision is taken together with ADR-004 (removing the baby fields) because
they touch the same files; they ship as one PR.

---

## Consequences

### What changes

- `models.py`: `LocalizedString` and `LocalizedParagraphs` added. Every
  `_en`/`_ru` flat pair on `NarrativeOutput` collapses into one
  `LocalizedString` (or `LocalizedParagraphs`).
- `llm/prompts.py`: the JSON skeleton in `SYSTEM_NARRATIVE` and
  `USER_NARRATIVE_TEMPLATE` is rewritten to match the nested shape and to ask
  for DE alongside EN and RU. Previous prompt versions kept as dated comments.
- `llm/narrative.py`: validation paths follow the new schema.
- `templates/memory.html.j2`: language toggle becomes three-way (EN / RU / DE).
  All field accesses move from `narrative.title_en` / `_ru` to
  `narrative.title.en` / `.ru` / `.de`.
- `tests/eval/judge.py` (and `judge_prompts.py`): the judge's `russian_fidelity`
  axis stays. A spot-check for DE is performed manually for the first 5
  generations under the new prompt; a `german_fidelity` axis is *not* added in
  this ADR — defer until DE has stabilised and we know what its failure modes
  look like.
- `tests/eval/cases/*.json`: unchanged in shape; the seed text drives generation,
  the golden output gets refreshed.
- `tests/eval/golden/*.json` and `*-judge.json`: regenerated under the new
  prompt and new schema.
- All mocks (`tests/conftest.py`, `tests/test_narrative.py`, `tests/test_renderers.py`,
  `tests/test_instagram.py`, `tests/test_cli.py`) construct `NarrativeOutput`
  via the new shape.
- `trailstory/renderers/instagram.py`: any direct flat-field access updated.

### What becomes easier

- Adding a fourth language is one Pydantic field, one prompt-skeleton edit,
  one template `{% if %}` arm, and a golden refresh — no model overhaul.
- Template authoring (and the upcoming three-style template variants — see
  ADR-006) is cleaner because every localised field has the same shape.
- The judge prompt's view of the narrative becomes hierarchical, which makes
  per-language scoring straightforward to extend later.

### What becomes harder

- Per-call LLM cost rises: the output token budget grows by roughly one
  language's worth (~1.3-1.5× output tokens, depending on density). At
  personal-use volume this is invisible; at hosted scale it is a per-request
  cost knob to monitor.
- One-shot generation of three languages stresses the model more than two.
  Quality on each language must be verified after the refresh — DE is the
  least-tested. The judge layer covers EN + RU; manual eyeball is the v0 gate
  for DE.
- All mocks across the test suite must be updated in one PR. Mechanical, but
  noisy diff.

### Follow-up

- After 5 real generations in DE, decide whether to add a `german_fidelity`
  axis to the judge. If DE consistently passes manual review, the judge
  remains EN/RU-focused. If DE shows specific recurring failures (e.g. flat
  prose, anglicisms, register slips), add the axis.
- If a fourth language is requested, this ADR's shape absorbs it without a
  new ADR. A new ADR is only needed if we depart from the
  one-call-many-languages strategy (e.g. moving DE to a separate translation
  pass for cost reasons).
