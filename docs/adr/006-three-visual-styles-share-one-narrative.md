# ADR 006 — Three visual styles share one narrative

**Date:** 2026-04
**Status:** Accepted — the concrete style lineup is superseded by
[ADR-020](020-style-lineup-letter-and-planned-styles.md) (2026-07):
`editorial` was renamed to `letter` ("The Letter"), `log` and
`encyclopedia` were removed, and four planned styles (zine, sunday,
postcard, album) reserve their slots. The core decision here — one
narrative prompt, many visual templates — stands unchanged.
**Decided by:** v0 product owner

---

## Context

v0 introduces a style picker on the builder form: the user chooses one of three
output looks before generation runs. The three named styles are:

- **Editorial** — Kinfolk / Exposure.co register: large serif headers, generous
  whitespace, single-column, full-bleed photos, restrained pull quotes.
- **Log** — utilitarian field-journal: sans-serif, compact, dated-entry feel,
  list-driven, smaller photos with captions, more "data" than "essay."
- **Encyclopedia** — kunstbuch / formal art-book: display serif, two-column,
  photo plates with figure captions, drop caps, formal register.

The product question this ADR settles: are these three *narratives* (three
prompt families generating different texts) or three *visual treatments of one
narrative*?

The current narrative voice — what the existing single prompt produces and
what `tests/eval/golden/` has been calibrated against — is the editorial
register. The eval suite (rubric + LLM judge, see ADR-003) is tuned for that
voice. The four judge axes (`warmth`, `narrative_arc`, `russian_fidelity`,
`photo_selection_plausibility`) all assume one register and score against
golden files that exemplify it.

If we ship three independent prompt families, each gets its own set of
goldens, its own judge calibration, and its own quality bar. Three times the
eval surface. Three times the prompt iteration overhead. All before a single
real user has indicated which of the three styles they actually pick.

---

## Options considered

### Option A — Three independent prompt families

Each style has its own `SYSTEM_NARRATIVE_*` and `USER_NARRATIVE_TEMPLATE_*`,
each generating a different *text* in addition to a different *layout*.

**Pros:**
- Maximum stylistic distinctiveness — log style can be terse and bullet-like,
  encyclopedia can be formal and footnote-y, editorial can be lyrical, and
  each register feels native rather than imported.
- Separation of concerns: a prompt change for one style cannot regress another.

**Cons:**
- 3× prompts, 3× golden files, 3× judge calibration. The current eval suite
  has been tuned for one voice for months; replicating that work twice more
  is a multi-week investment with no usage signal yet to justify it.
- Per-call cost for prompt iteration triples (`make eval` runs against three
  prompt families).
- The user has not validated that distinct *texts* are what they want. They
  have stated that distinct *visual treatments* are what they want. Building
  more than that is speculative scope.
- Risk of fragmenting the product identity: "what does Trailstory sound like?"
  becomes three answers instead of one.

### Option B — One narrative prompt, three visual templates (chosen)

A `Style` enum on `Memory` selects which Jinja template + CSS bundle renders
the same `NarrativeOutput`. The text is identical across styles. The layout,
typography, photo treatment, and accent colours differ.

**Pros:**
- One prompt to maintain. One quality bar. One golden set per case (now
  multiplied by three languages from ADR-005, but not by three styles on top).
- The eval suite's existing tuning carries forward unchanged.
- Cheap to ship: three Jinja templates plus three CSS files, no new prompt
  artistry, no new judge axes.
- The user can validate the styles in real use. If one is consistently picked,
  invest in it. If one is never picked, drop it. The investment is reversible.
- Adding a fourth visual style later is templates and CSS only — does not
  reopen prompt-family questions.

**Cons:**
- The editorial voice rendered in a utilitarian "field-log" layout is a slight
  register mismatch: lyrical prose in Moleskine clothing. For most readers this
  is invisible or even charming; for some it may feel like the layout and the
  prose are pulling in different directions.
- The encyclopedia layout in particular tends to imply a more formal voice
  ("On the morning of...") than the editorial voice produces. The mismatch is
  largest here.
- We are deferring a real choice ("should the log style sound like a log?")
  rather than making it.

### Option C — One prompt, three visual templates, **plus** per-style prompt suffixes

Same as Option B, but each style appends a short tone-nudge to the prompt
("Write in a terse, observational register suitable for a field log") so the
text shifts modestly per style without forking the whole prompt.

**Pros:** Reduces the register mismatch from Option B at low cost. Suffixes
can be ~50 tokens, eval-able as small additions, easy to revert.
**Cons:** Still introduces three quality bars (one per suffix combination).
Goldens technically need refreshing per suffix or we have to accept that
the rubric/judge run only against the no-suffix baseline. The complexity
ratchet starts here even if the immediate cost is low.

---

## Decision

**Use Option B — one narrative prompt, three visual templates.** A `Style`
enum drives template selection. The same `NarrativeOutput` renders three
ways.

Option C remains a deliberate v1 escape hatch. If real usage shows the
register mismatch is annoying — measured by user feedback or by judge scores
on style-specific goldens — we add per-style suffixes then. We do not
pre-build the suffix infrastructure.

---

## Consequences

### What changes

- `models.py`: a `Style` enum with values `editorial`, `log`, `encyclopedia`
  is added. `Memory` gets a `style: Style = Style.editorial` field.
- `templates/`: the current `templates/memory.html.j2` body is moved to
  `templates/styles/editorial.html.j2`, preserving its current visual
  identity as the polished default. Two new style templates are added:
  `templates/styles/log.html.j2` and `templates/styles/encyclopedia.html.j2`.
  The top-level `templates/memory.html.j2` becomes a thin shell that
  `{% include %}`s the right style template based on `meta.style`.
- The Jinja context shape is identical across all three styles. The rendering
  path differs only in layout and CSS.
- `renderers/html.py`: passes `memory.style` into the Jinja context.
- `cli.py`: gains a `--style {editorial,log,encyclopedia}` option, default
  `editorial`.
- `tests/test_styles.py`: new file. Renders the same `Memory` three times,
  asserts each output contains style-specific markers (CSS class names or
  distinct structural elements). Confirms the *narrative text* is identical
  across the three.
- The web builder (a future ADR / future PR) exposes a radio-button picker
  with the three styles, defaulting to editorial.

### What becomes easier

- Prompt iteration stays single-tracked. One change, one eval run, one set of
  goldens to refresh. ADR-003's eval discipline carries forward without
  multiplication.
- Adding a fourth visual style is a templates-and-CSS PR, no prompt work.
- The product story stays coherent: Trailstory has one voice, three looks.

### What becomes harder

- Visual QA must cover three styles for any change that touches shared markup
  (the language toggle, the photo block, the elevation profile SVG).
  `make test-render` will need to render all three in one command.
- The `log` and `encyclopedia` styles ship in v0 with editorial-voice text.
  If the register mismatch is loud, the v0 user (initially: us) will feel
  it before any external user does — which is the intended early-warning
  signal for whether Option C becomes necessary.
- The per-style template files duplicate structural HTML (header, footer,
  language toggle, photo grid). Some markup factoring via Jinja macros is
  worth doing as templates grow — but that is a refactor when the second
  template lands, not now.

### Follow-up

- After 5 real generations in each style (15 hikes; or 5 hikes rendered
  three ways), reassess whether per-style prompt suffixes are needed.
  Decision criterion: does the editorial voice in the log layout feel
  *wrong* to the reader, not just *different*? If yes, write ADR-007 for
  Option C. If no, leave Option B as the steady state.
- If a fourth style is requested, this ADR's pattern absorbs it. New ADR
  only if we depart from "one prompt, many templates."
