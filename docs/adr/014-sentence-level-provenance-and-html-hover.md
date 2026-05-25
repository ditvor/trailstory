# ADR 014 — Sentence-level provenance + HTML hover (Phase 4)

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

Phases 0-3 closed the writer's fabrication budget structurally: the
ledger constrains what facts exist, vision adds photo-grounded facts,
the writer can only reference what the ledger contains. But the
reader of the rendered HTML page still gets no signal about which
sentences are seed-quoted, which are photo-grounded, and which are
literary reconstruction the writer chose to add.

The Bourdain-quiet voice the writer produces by default is full of
mood and atmospheric prose. Some of that is grounded ("Danny waved
at the lake" — lake from photo, waving in seed text). Some is
the writer's own embellishment ("the afternoon felt like something
on loan" — no specific source, just tone). Without provenance, the
reader can't tell the two apart, and the writer's natural tendency
to overlay mood erodes the trust the rest of the pipeline is built
to preserve.

Phase 4 makes the trust visible: every sentence carries a
`Provenance` tag pointing at its source. The HTML output shows
INFERRED sentences with a subtle tint and exposes the provenance
on hover. The user can see exactly what the writer is leaning on.

Phase 4 also creates the data structure Phase 4.1 (the builder edit
mode UI) needs to let the user accept / edit / delete inferred
sentences before publishing. This ADR scopes the schema + prompt +
rendered HTML; the edit UI is a separate follow-up.

---

## Options considered

### Option A — Sentence as first-class type with tri-lingual text + one Provenance (chosen)

`Sentence` is a Pydantic model with `text: LocalizedString` and
`provenance: Provenance`. `Paragraph = list[Sentence]`.
`NarrativeOutput.paragraphs: list[Paragraph]`. The writer is told to
keep EN/RU/DE sentence-aligned (one EN sentence = one RU sentence =
one DE sentence) so each language renders the same provenance hint
on the same sentence.

**Pros:**

- One provenance per semantic unit. The question "why is this
  sentence here?" has one answer that doesn't depend on which
  language the reader is viewing.
- Tri-lingual stays inside the sentence. The writer's existing
  EN/RU/DE alignment workflow is unchanged; the model already
  produces sentence-aligned translations.
- Renderable by walking the list once. HTML template wraps each
  sentence's per-language text in a `<span data-prov="...">`; the
  CSS selector `[data-prov="inferred"]` does the tint.
- Backwards-compatible read path. `paragraphs_as_localized()` joins
  sentence texts per language into the old `LocalizedParagraphs`
  shape so code that doesn't care about provenance (carousel
  renderer, the eval rubric's paragraph-count check, the log /
  encyclopedia style templates) keeps working without per-call
  refactoring.

**Cons:**

- Schema-bumping change. `schema_version: int = 3` invalidates every
  pre-Phase-4 cache entry and every eval golden. All test fixtures
  need updating (helper functions `paragraphs_from_strings` and
  `paragraphs_dict_from_strings` in `tests/conftest.py` absorb the
  boilerplate).
- Writer prompt complexity rises. The model now has to produce
  sentence-aligned tri-lingual output AND tag provenance. Pilot
  shows Opus handles it cleanly; a future cheaper writer model may
  struggle.
- Per-paragraph sentence count is now visible to the rubric. The
  3-5 paragraph check survives via the helper; a future "sentences
  per paragraph in [2, 5]" check is a follow-up.

### Option B — Parallel provenance list aligned by index

Keep `paragraphs: LocalizedParagraphs` (flat per-language string
lists). Add a parallel
`paragraphs_provenance: list[list[Provenance]]` where
`provenance[i][j]` lines up with sentence `j` of paragraph `i`.

**Pros:** Less invasive schema change. Existing code paths unchanged.
**Cons:**

- Brittle. Sentence count per language can drift (RU translation
  splits one EN sentence into two); index alignment breaks
  silently. Option A makes alignment a Pydantic invariant.
- The writer prompt has to coordinate two structures (paragraphs
  AND a parallel provenance array). Pilot found this confusing —
  the model frequently produced mismatched lengths.
- "Why is this sentence here?" answers require looking up two
  fields by index. Renderers and the future edit UI become more
  brittle.

### Option C — Provenance on paragraphs, not sentences

One Provenance per paragraph. Coarser-grained but simpler.

**Pros:** Fewer tags to manage. Smaller prompt change.
**Cons:**

- Loses the resolution the user actually needs. A paragraph
  typically contains one seed-grounded fact and three sentences of
  mood; a per-paragraph tag (SEED or INFERRED) is misleading
  either way. Per-sentence is the right granularity.

### Option D — Defer Phase 4 entirely; ship Phase 2.5 + 3.1 + 3.2 without it

Argue that the user already has the ledger + vision + verifier
combo from earlier phases; sentence-level provenance is gilding.

**Pros:** Smallest PR, no schema bump.
**Cons:**

- The verifier (ADR-011) depends on sentence-level provenance to
  compute its INFERRED-ratio signal. Skipping Phase 4 forces
  Phase 2.5 back to the paid-judge variant (Option B in ADR-011)
  or removes it entirely. Phase 4 makes Phase 2.5 free.
- The user has been asking for "show me why this sentence is
  here" since the very first Bad Tölz audit. Phase 4 is the
  shipping answer.

---

## Decision

**Adopt Option A.** New types in `trailstory/models.py`:
`ProvenanceSource` (StrEnum: `SEED`, `PHOTO`, `GPX`, `INFERRED`),
`Provenance` (frozen, source + reference), `Sentence` (frozen,
text + provenance), `Paragraph = list[Sentence]` (type alias).
`NarrativeOutput.paragraphs` becomes `list[Paragraph]`;
`schema_version` bumps to `3`. A new
`paragraphs_as_localized()` method on `NarrativeOutput` joins
sentence texts per language into the old `LocalizedParagraphs`
shape for legacy consumers (the rubric, the log / encyclopedia
templates, the Instagram carousel).

The writer prompt rewrites the JSON skeleton to ask for the
new structure. The previous Phase 2 skeleton is preserved as a
dated comment for revertability. The prompt explicitly enumerates
the four provenance source values and provides honesty framing
("if you can't point at a ledger entry that supports it, this is
inferred, not seed").

The editorial style template gets the full treatment: each
sentence wrapped in `<span class="sent" data-prov="...">` with
a title attribute carrying source + reference; INFERRED sentences
get a subtle background tint via CSS. The log and encyclopedia
templates use the `paragraphs_as_localized()` fallback for now —
Phase 4.1 will port them.

---

## Consequences

### What changes

- `trailstory/models.py`: `ProvenanceSource` enum, `Provenance`,
  `Sentence` models, `Paragraph` type alias.
  `NarrativeOutput.paragraphs` shape change, `schema_version=3`,
  new `paragraphs_as_localized()` method.
- `trailstory/llm/prompts.py`: rewritten writer JSON skeleton and a
  new "Provenance source values" rubric paragraph. Previous shape
  preserved as a dated comment.
- `trailstory/renderers/html.py`: passes both `narrative` and a
  precomputed `flat_paragraphs` into the template context.
- `templates/styles/editorial.html.j2`: walks the new structure,
  wraps each sentence in `<span class="sent" data-prov="...">`
  with title hover, adds CSS for the INFERRED tint.
- `templates/styles/log.html.j2`, `templates/styles/encyclopedia.html.j2`:
  use `flat_paragraphs.en/ru/de` instead of `narrative.paragraphs.en/ru/de`.
  Deliberate Phase 4.1 follow-up to port them to sentence-level rendering.
- `tests/eval/rubric.py`: every paragraph-aware check now calls
  `narrative.paragraphs_as_localized()` to keep working against the
  new structure.
- `tests/conftest.py`: two new helpers — `paragraphs_from_strings`
  (builds a `list[Paragraph]` from per-language string lists) and
  `paragraphs_dict_from_strings` (builds the JSON-dict shape for
  fake-LLM-response tests).
- `web/dev.py`: fake-LLM `_FAKE_NARRATIVE` constant rewritten for
  the new shape via a small `_fake_paragraph` helper.
- All test fixtures across `tests/test_*.py` updated for the new
  shape via the helpers.
- `CHANGELOG.md` and `CLAUDE.md` decision register get entry 14.

### What becomes easier

- Phase 4.1 (the builder edit UI) consumes a clean
  `list[list[Sentence]]` structure. Click-to-edit per sentence is
  straightforward; provenance lookup is one attribute access.
- The verifier (ADR-011) gets its INFERRED-ratio signal for free
  by walking the same structure.
- Future "by language" filters (show only INFERRED sentences when
  switching to a fast-review mode) are easy template changes.

### What becomes harder

- Schema-version bump invalidates every existing cache entry and
  every eval golden. First `make eval-update-golden` after this
  refreshes all 4 cases × 2 (narrative + judge) golden files.
- The writer prompt is materially longer (the JSON skeleton has
  more nesting, plus the new provenance-source rubric paragraph).
  Output tokens rise modestly; pilot shows ~10-15% more tokens per
  narrative. Within the per-render cost ceiling.
- Two of three style templates use the flat fallback rendering;
  visually they're identical to pre-Phase-4 output. Phase 4.1
  needs to port them so the hover UX is consistent across styles.

### Known limitations (deliberately not in this PR)

- **No builder edit mode.** The user can SEE provenance via the
  hover UI but cannot edit / remove inferred sentences before
  publishing. Phase 4.1 follow-up.
- **Sentence count not gated by the rubric.** A paragraph with one
  sentence still passes the rubric; a paragraph with twenty does
  too. Tightening is a Phase 4.2 if real renders show drift.
- **Log + Encyclopedia templates rendered without provenance.**
  They use the flat fallback; the sentence spans don't surface
  there. Phase 4.1 ports them.
- **Provenance is writer-self-reported.** A writer that tags
  fabricated sentences as SEED defeats the trust UI. The eval
  judge catches drift offline; runtime defence is the verifier
  (ADR-011); structural defence is Phase 4.1's user editing.
