# ADR 015 — Chapter-based narrative for the Trailpath layouts

**Date:** 2026-05
**Status:** Proposed
**Decided by:** v0 product owner

---

## Context

The Trailpath design handoff (May 2026) brings four new output styles —
Zine, Sunday, Postcard Set, Album — alongside a redesigned Letter. All
five layouts consume a uniform data contract:

```ts
type L = { en: string; ru: string };           // EN + RU only in the handoff
type Chapter = {
  id: string;
  time: string;                                 // "12:04"
  place: L;
  coord: { lat: number; lon: number };
  title: L;
  body: L;                                      // ~80–120 words
  photo: { ratio, caption: L, src, focus? };
};
type Hike = {
  meta: { … },
  dek: L,
  chapters: Chapter[],                          // exactly 6
  pullQuotes: { en: string[], ru: string[] },
  track: [number, number][], waypoints, elevation,
};
```

That is, every layout renders **six chapters** with per-chapter time,
place, title, body, and a one-to-one bound photo. The Zine pagination,
the Sunday "STOP 01–06" coral badges, the Postcard front+back-per-card
metaphor, and the Album polaroid-per-page rhythm all assume that
binding. Photos are not a free-floating gallery interleaved through
prose; each photo is *the* photo for *that* chapter.

The current `NarrativeOutput` is shaped differently:

```python
class NarrativeOutput(BaseModel):
    schema_version: int = 3
    title: LocalizedString                      # EN / RU / DE
    subtitle: LocalizedString
    paragraphs: list[Paragraph]                 # 3–5 paragraphs of Sentences
    pull_quote: LocalizedString
    milestone: LocalizedString
    selected_photo_indices: list[int]           # 6–8 indices into PhotoMeta[]
```

There is no chapter envelope, no per-paragraph time or place, and the
photo–paragraph relationship is unspecified (`renderers/html.py`
interleaves one photo per paragraph after the first; any overflow falls
after the pull quote). The existing editorial template
([`templates/styles/editorial.html.j2`](../../templates/styles/editorial.html.j2))
embraces this flat shape — sentence-level provenance is per-sentence
inside a flat list of paragraphs (ADR-014, Phase 4).

The product decision now is: can we render the Trailpath layouts
faithfully against the existing flat shape, or does the data model need
to grow a chapter envelope?

Related constraints already on the books:

- **ADR-001** — the rendered HTML is self-contained (photos as base64).
  Chapter binding does not change this; it just changes which photo
  base64 lives next to which prose block.
- **ADR-005** — every user-facing string is `LocalizedString { en, ru, de }`.
  The handoff is EN + RU; we keep DE everywhere we already have it.
- **ADR-006** — *one prompt, many visual treatments.* This ADR is the
  first real test of that principle: do the new styles share a single
  writer-pass output, or does the chapter-binding requirement amount to
  a new prompt family?
- **ADR-008 / ADR-009 / ADR-010 / ADR-011 / ADR-014** — the writer's
  fabrication budget is constrained by a `FactLedger` (Person, Beat,
  weather, photo descriptions), and every sentence carries a
  `Provenance` tag (SEED / PHOTO / GPX / INFERRED). New per-chapter
  fields (`time`, `place`) are *new concrete claims* and inherit that
  contract: they must be supported by GPX waypoints or refuse to render.

The picker already names the four new styles. They are currently shown
as `coming_soon=True` "SOON" cards in [`web/copy.py`](../../web/copy.py).
The legacy `Style.log` and `Style.encyclopedia` enum values, plus
their templates, are vestigial: never surfaced in the picker, never
shipped to users, and the v0 product decision (recorded inline in
[`web/copy.py`](../../web/copy.py) and reaffirmed in the May 2026 handoff
review) is to land the five Trailpath styles only.

---

## Options considered

### Option A — Additive `chapters` field on `NarrativeOutput` (chosen)

Add a `Chapter` model and put `chapters: list[Chapter]` on
`NarrativeOutput`. Bump `schema_version` to `4`. The writer pass
produces chapters; the new layouts read chapters directly; the
existing Letter template either keeps reading flat paragraphs (A1)
or one-line-switches to chapter bodies (A2).

```python
class Chapter(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Stable id ("arrival", "river", "dinner") — used as DOM anchor and
    # for chapter-rail nav. Slug from title.en at writer time.
    id: str
    # GPX-derived. Picked from the timestamp of the photo bound to this
    # chapter (its EXIF time, snapped to the nearest GPX waypoint).
    # Format: "HH:MM" 24-hour. Provenance: GPX.
    time: str
    # Locality near coord — either reverse-geocoded (if a geocoder is
    # configured) or HikeInput.location_name as a coarse fallback.
    # Provenance: GPX + location_name.
    place: LocalizedString
    # Latitude / longitude from the bound photo's EXIF or its nearest
    # GPX waypoint. Used by some layouts (Zine route postmark, Album
    # rubber stamp date+location).
    coord: tuple[float, float]
    # The chapter title — short noun phrase. Sentence-level provenance
    # not surfaced for the title; the writer is told to keep it concrete
    # (place-name, beat-name) and the rubric/judge gate that as part of
    # the faithfulness axis.
    title: LocalizedString
    # The chapter body — same Paragraph shape used today for narrative
    # paragraphs (list[Sentence] with tri-lingual text + provenance).
    # ~2–4 sentences per chapter, ~80–120 words English equivalent.
    body: Paragraph
    # Index into Memory.selected_photos. Exactly one photo per chapter.
    # The renderer reads photo bytes from selected_photos[photo_index].
    photo_index: int
```

`NarrativeOutput` grows the field. **A1** keeps both shapes side-by-side
during the migration; **A2** treats chapters as the single source of
truth and reduces flat paragraphs to a computed view.

```python
# A1 — both shapes side-by-side. chapters is Optional; gates new styles.
class NarrativeOutput(BaseModel):
    schema_version: int = 4
    title: LocalizedString
    subtitle: LocalizedString
    paragraphs: list[Paragraph]              # legacy primary; Letter reads this
    chapters: list[Chapter] | None = None    # new; required for the four new styles
    pull_quote: LocalizedString
    milestone: LocalizedString
    selected_photo_indices: list[int]

# A2 — chapters is canonical; paragraphs computed.
class NarrativeOutput(BaseModel):
    schema_version: int = 4
    title: LocalizedString
    subtitle: LocalizedString
    chapters: list[Chapter]                  # canonical, always 6
    pull_quote: LocalizedString
    milestone: LocalizedString
    # paragraphs_as_localized() walks chapters[*].body for legacy consumers
    # (Instagram carousel, rubric, the Letter template if it stays flat).
    selected_photo_indices: list[int]        # = [c.photo_index for c in chapters]
```

**Pros (shared between A1 and A2):**

- Future-proof: the data shape matches the design contract every new
  layout expects. New styles ship as a template + CSS + a `coming_soon`
  flag flip.
- Faithfulness extends cleanly: `chapter.time` and `chapter.place` are
  *new concrete claims* and inherit the existing provenance regime.
  Time is `GPX`, place is `GPX` (or `INFERRED` if no geocoder).
  Per-sentence provenance inside `chapter.body` is unchanged from
  ADR-014.
- Photo binding becomes explicit. Today the renderer guesses how to
  interleave; under A the writer (or a small post-writer pass) commits
  to a binding once, and every renderer reads it. No more "the carousel
  picks photo 0 but the editorial template puts photo 0 next to
  paragraph 1" implicit alignment.
- ADR-006 is preserved: still one writer pass, still one
  `NarrativeOutput`, just richer. The four new layouts are visual
  treatments of the same narrative, not new prompt families.

**Cons (shared):**

- Schema bump. Every cache entry and every eval golden invalidates.
  First `make eval-update-golden` after this is paid (writer call × 4
  cases) and re-baselines the rubric. `make eval-live` adds the judge
  call. Same routine as the ADR-014 refresh.
- Writer prompt grows. The JSON skeleton has to teach the model the
  chapter envelope on top of the existing sentence-level provenance.
  Output tokens rise an estimated 30–50% (six chapter envelopes ×
  three languages × ~3 sentences). Within the per-render cost
  ceiling; not free.
- The chapter count becomes load-bearing. Today the writer chooses
  3–5 paragraphs and 6–8 photos; under A it must produce exactly six
  chapter envelopes, each bound to exactly one of the selected
  photos. The "select 6–8, prose chooses how many" flexibility is
  gone — six is the design contract.
- The `selected_photo_indices: list[int]` field stays but becomes
  redundant under A2 (it's `[c.photo_index for c in chapters]`).
  Under A1 it stays independent; the Letter renderer keeps reading it.

**A1 vs A2:**

- A1 is the smaller migration. The existing Letter template, eval
  rubric, and Instagram carousel keep reading
  `narrative.paragraphs` / `narrative.selected_photo_indices` exactly
  as they do today. Risk: drift between `paragraphs` and
  `chapters[*].body` if a future writer change updates one and not the
  other. Mitigation: a model-level validator that checks the two are
  consistent (every sentence in `paragraphs` appears in some
  `chapters[*].body`, modulo order).
- A2 is one source of truth — but the migration touches every
  paragraph-consuming site. The Letter template's
  `for paragraph in narrative.paragraphs` becomes
  `for chapter in narrative.chapters … for sentence in chapter.body`;
  the rubric's paragraph-count check becomes a chapter-count check;
  the Instagram carousel reads
  `narrative.paragraphs_as_localized()` (already exists, just rewires
  to walk chapters).

**Recommendation:** A2. The "two sources of truth" risk of A1 is real,
and the Letter template change is one accessor change behind a
helper. But A1 is acceptable if the v0 priority is "ship the four new
styles without touching the Letter at all". Open question.

### Option B — Compute chapters at render time from `paragraphs` + `selected_photo_indices`

The data model stays as it is today. A new
`trailstory/chapters.py` heuristic takes `NarrativeOutput.paragraphs`,
the `selected_photos` (with EXIF timestamps and GPS), and the GPX
waypoints, and produces `list[Chapter]` at render time.

**Pros:** No schema change. No prompt change. No eval refresh.

**Cons:**

- Lossy. Paragraphs are not chapters: paragraph count today is 3–5,
  chapter count required is 6. The heuristic would have to either
  collapse photos to paragraphs (lose chapter granularity) or split
  paragraphs by sentence count (chapter title and body would be
  invented at render time, not authored).
- The chapter `title` is a new piece of writing. Inventing it
  client-side violates the faithfulness contract (no LLM call → no
  prompt → no provenance). Either we add a second post-writer LLM
  call (worse than A: now two writer-class calls per render), or
  we use the sentence-leading text as the title (untenable
  editorially).
- Per-chapter `time` and `place` would be derived deterministically
  from the photo's EXIF + nearest GPX waypoint, which is fine — but
  the title and body still need a creative author. B is just A with
  more friction.

Rejected: the chapter-as-render-time-view requires inventing the
exact chapter envelope the writer already produces under A, except
without the writer.

### Option C — Build the new styles against the flat `paragraphs` model

Render Zine, Sunday, Postcard, Album using the existing data: the
title and prose from `paragraphs`, photos interleaved by the
existing `renderers/html.py` algorithm, no per-photo time or place.

**Pros:** Smallest change. No prompt work. No eval refresh.

**Cons:**

- The design intent is lost in every layout. Zine's "01 / The
  square at noon, p.2" contents page can't exist without per-chapter
  time and title. Sunday's "STOP 01 · 12:04 · Parking, Marktstraße"
  coral badge can't exist. Postcard's front+back-per-card metaphor
  collapses to a generic photo-with-paragraph card. Album's
  rubber-stamped chapter numbers + per-photo handwritten captions
  lose the chronology they're meant to carry.
- The handoff explicitly bakes the chapter-per-photo binding into
  every visual treatment. Building around it is the equivalent of
  shipping the layouts in a fundamentally different register from
  the design.
- We'd have to re-record the design promise in the picker
  (`STYLE_CARDS` text) and in the future ADR if a chapter-aware
  variant is ever requested. The shape grows back to A eventually,
  having paid C's costs in the meantime.

Rejected: trades a one-time schema migration for a permanent visual
debt.

### Option D — Per-style prompt families (the inverse of ADR-006)

Each new style gets its own writer prompt and golden suite. Zine
prompt produces six terse 80-word chapter bodies; Album prompt
produces six 60-word photo captions in handwritten register; etc.

**Pros:** Maximum stylistic distinctiveness per style. Each can be
calibrated independently.

**Cons:**

- Reverses ADR-006. Five prompts, five golden suites, five
  calibrations. The eval discipline cost goes from one writer call
  per case to five, on top of the chapter envelope work.
- The product story fragments: Trailstory has one voice, today. Five
  voices is a different product.
- Premature optimisation. The user has not validated that distinct
  texts (not just distinct visuals) are what they want for the new
  styles; building five prompt families before that signal is
  speculative.

Rejected: identical reasoning to ADR-006 option A. The escape hatch
remains — per-style suffixes can be added later if real usage shows
the editorial register clashes with, say, the Postcard layout.

---

## Decision

**Adopt Option A2 — `chapters` becomes the canonical narrative shape
on `NarrativeOutput`. `schema_version=4`.** Flat per-language
paragraphs are reduced to a computed view via
`paragraphs_as_localized()`, which walks `chapters[*].body` and
joins sentences per language for legacy consumers (the Instagram
carousel, the eval rubric's paragraph-count check, the existing
Letter template until it ports). One source of truth; the
"drift between two parallel fields" risk of A1 does not exist.

**The chapter envelope is produced by the existing writer pass
(A-writer).** The writer's JSON skeleton is extended to instruct the
model on the six-chapter contract, on top of the existing sentence-
level provenance. One LLM call per render, +30–50% output tokens.
A-postwriter (a separate Haiku-class chapter pass) stays as the
escape hatch if pilot shows the writer can't keep chapter count or
photo binding reliably across EN/RU/DE — but is not built
preemptively.

Concretely, PR 1 of the rollout will:

1. Add `Chapter` to `trailstory/models.py`. `NarrativeOutput.chapters`
   is required (A2); `paragraphs` field is removed;
   `paragraphs_as_localized()` becomes a computed accessor over
   `chapters[*].body`. `selected_photo_indices` derives from
   `[c.photo_index for c in chapters]` and is dropped as a separate
   field. `schema_version=4`.
2. Drop `Style.log` and `Style.encyclopedia` from
   `trailstory/models.py`, delete `templates/styles/log.html.j2`
   and `templates/styles/encyclopedia.html.j2`, remove their
   references from `tests/test_styles.py`, `web/pipeline.py`,
   `web/routes.py`, and the CLI's `--style` choices. The four new
   styles in the picker (`STYLE_CARDS`) stay `coming_soon=True`
   in PR 1; the flags flip one at a time as each renderer ships
   (PR 2 = Zine, PR 3 = Postcard, PR 4 = Sunday, PR 5 = Album).
3. Rewrite the writer JSON skeleton in `trailstory/llm/prompts.py`
   to instruct the model on chapter envelopes (six exactly, each
   bound to one of the selected photos), keeping previous skeleton
   as a dated comment per the project's prompt-versioning rule.
4. Refit the rubric (`tests/eval/rubric.py`) for chapter-count and
   chapter-photo-binding checks. Keep the paragraph-count check
   working via the `paragraphs_as_localized()` view.
5. Refresh all four eval cases (`tests/eval/cases/*`) and goldens
   (`tests/eval/golden/*` + `*-judge.json`) via
   `make eval-update-golden`. Paste rubric + judge tables in the
   PR description. **This is the paid step** — one writer call +
   one judge call per case.
6. Wire the Letter template (`templates/styles/editorial.html.j2`)
   to the new shape. The current `for paragraph in narrative.paragraphs`
   loop changes to `for paragraph in narrative.paragraphs_as_localized()`
   (or walks `chapters[*].body` directly — pick whichever keeps the
   sentence-level provenance render unchanged). Visible output must
   stay pixel-identical to the current PR-57 baseline.
7. Do **not** ship any of the four new templates in PR 1. The point
   of PR 1 is to migrate the data shape with zero new user-facing
   surface, so the eval refresh is a controlled variable.

The chapter envelope's `time` field is derived deterministically
from the bound photo's EXIF timestamp (or, if EXIF is absent, the
nearest GPX waypoint). The `place` field is `HikeInput.location_name`
in v0 (no reverse-geocoder); a follow-up ADR can introduce
per-coordinate locality lookup. Both are surfaced through the
existing `Provenance` regime: time → `GPX`, place → either `GPX` (if
the coord came from a waypoint) or `SEED` (if it's the location_name
fallback).

---

## Consequences

### What changes

- `trailstory/models.py`: new `Chapter` model.
  `NarrativeOutput.chapters: list[Chapter]` (required, exactly 6)
  replaces the flat `paragraphs` and `selected_photo_indices` as
  canonical state; `paragraphs_as_localized()` becomes a computed
  accessor that walks `chapters[*].body`. `schema_version` bumps
  to `4`. `Style.log` and `Style.encyclopedia` removed from the
  enum.
- `trailstory/llm/prompts.py`: writer JSON skeleton rewritten to
  include the chapter envelope. Previous skeleton preserved as a
  dated comment.
- `trailstory/llm/narrative.py`: validation paths walk the new
  shape. Verifier (ADR-011) recomputes the INFERRED ratio against
  the union of every chapter's body, not the flat paragraphs.
- `trailstory/renderers/html.py`: passes `chapters` (and any
  derived data like the pre-bound photo for each chapter) into the
  Jinja context. Photo interleave logic is removed — chapters carry
  their own photo binding.
- `templates/styles/editorial.html.j2`: re-targets the chapter
  shape. The `for paragraph in narrative.paragraphs` loop becomes
  either `for paragraph in narrative.paragraphs_as_localized()` (one-
  line accessor swap, smallest diff) or walks `chapters[*].body`
  directly (preserves the sentence-level `data-prov` spans without an
  intermediate join). Either path keeps the visible output identical
  to the current PR-57 baseline.
- `templates/styles/log.html.j2`, `templates/styles/encyclopedia.html.j2`:
  deleted.
- `web/copy.py`: `STYLE_CARDS` order stays. The four new cards
  remain `coming_soon=True` until their respective renderer PRs.
- `tests/eval/cases/*.json`, `tests/eval/golden/*.json`,
  `tests/eval/golden/*-judge.json`: refreshed. Paid: one writer
  call + one judge call per case. Score table pasted in the PR.
- `tests/conftest.py`: `paragraphs_from_strings` /
  `paragraphs_dict_from_strings` helpers grow `chapter_from_*`
  siblings.
- `tests/test_styles.py`: drops log/encyclopedia assertions; keeps
  editorial assertions; placeholder cases for the four new styles
  land empty (to be filled by PRs 2–5).
- `CHANGELOG.md`, `CLAUDE.md`: decision register gets entry 15.

### What becomes easier

- All four new layouts ship as a Jinja template + CSS + one font
  bundle each. No prompt work per style. ADR-006's "one voice,
  many looks" pattern absorbs the new styles cleanly.
- Photo-to-prose alignment becomes a first-class data fact. No more
  guessing at render time. The Instagram carousel can read
  `chapters[i].photo_index` and `chapters[i].body` to caption each
  slide with the right beat.
- Future styles (a sixth or seventh layout) are pure template work.
- The chapter envelope creates the data structure for "edit this
  chapter" (Phase 4.1 follow-up — per-chapter accept/edit/remove,
  not just per-sentence).

### What becomes harder

- The writer's job grows. Pilot will tell us whether Opus reliably
  produces six well-bound chapters across EN/RU/DE with sentence-
  level provenance preserved; if not, A-postwriter is the escape
  hatch.
- Per-render output token cost rises 30–50%. At personal-use
  volume, invisible; at hosted scale, monitor.
- The chapter count is now hard-coded at six. A two-photo hike
  (~30-minute walk) and a twelve-photo hike (~all-day traverse)
  both have to fit the six-chapter mould. The writer is told to
  collapse or distribute as needed; the rubric checks for empty
  chapters. A future ADR may relax this to "4–8 chapters" if the
  six-only constraint produces awkward bunching.
- Eval calibration is one writer-pass change away from the v0
  baseline. The judge axes (`warmth`, `narrative_arc`,
  `russian_fidelity`, `photo_selection_plausibility`,
  `faithfulness`) all need to be re-scored against chapter-shaped
  output; the regression gate compares against refreshed goldens.

### Known limitations (deliberately not in this PR)

- **Reverse geocoding for `place`.** v0 falls back to
  `HikeInput.location_name`. A follow-up ADR can add a deterministic
  lookup (OpenStreetMap Nominatim, a local geocoder cache, etc.) to
  populate per-chapter localities from `coord`.
- **Chapter editing UI.** The builder lets the user accept the
  generated narrative as-is; per-chapter accept/edit/remove is a
  Phase 4.1 follow-up. Phase 4 (ADR-014) already gives per-sentence
  provenance; chapter editing is the next layer.
- **Per-style register tuning.** ADR-006's escape hatch — per-style
  prompt suffixes for register match — stays unbuilt until real
  user feedback shows the editorial voice clashes with, say, the
  Postcard postcard-back-message register.
- **Letter visible-output redesign.** The Trailpath handoff also
  redesigns the Letter (chapter-aware editorial). PR 1 keeps the
  current PR-57 Letter visible output. A future PR can bring the
  handoff's Letter design forward; this ADR does not commit to it.

### Follow-up

- After PR 1 lands and the four renderer PRs (Zine / Postcard /
  Sunday / Album) ship, reassess whether A-postwriter is needed.
  If the writer reliably hits six well-bound chapters under
  A-writer, leave it. If chapter-count drift or photo-binding
  errors appear in eval, write ADR-016 for A-postwriter.
- After the first 5 real renders per new style, decide whether
  per-style prompt suffixes (ADR-006 Option C) are needed.
