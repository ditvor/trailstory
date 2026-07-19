# ADR 020 — Style lineup: The Letter shipped, four styles planned, log and encyclopedia removed

**Date:** 2026-07
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

ADR-006 settled that visual styles are *rendering treatments of one
narrative*, not prompt families, and shipped three of them: `editorial`,
`log`, and `encyclopedia`. That structural decision has held up well and is
not being reopened here.

What did not hold up is the lineup itself. The builder reskin ("The Letter"
compose page) adopted the Claude Design proposal for the style picker, which
names five styles with distinct visual promises:

- **The Letter** — long-form prose with a quiet voice, marginalia for the
  map and stats. This is exactly the existing `editorial` renderer.
- **The Zine** — riso / two-color / halftone duotone, designed to be
  printed and mailed.
- **Sunday** — joyful weekend-day treatment, cream + sun-yellow + coral,
  STOP badges.
- **Postcard Set** — mid-century travel cards, one postcard per chapter.
- **Album** — scrapbook register: polaroid frames, washi tape,
  handwritten captions.

The legacy `log` and `encyclopedia` renderers match none of these cards.
They were already hidden from the web picker because their visual treatment
does not deliver what any card promises, which left them reachable only via
the CLI `--style` flag — unshown, unmaintained, but still multiplying the
visual-QA surface (every shared-markup change had to be eyeballed three
ways) and still carrying golden files.

## Decision

1. **Rename `editorial` → `letter`** everywhere: the `Style` enum value,
   `templates/styles/letter.html.j2`, the fonts directory
   (`templates/fonts/letter/`), the embedded font-family names
   (`Letter Serif` / `Letter Mono`), the body marker class
   (`style-letter`), and the web form value.
2. **Delete the `log` and `encyclopedia` renderers**: templates, enum
   values, goldens, and style-specific tests. No deprecation period — they
   were never surfaced to a real user.
3. **The `Style` enum carries the full product lineup** (`letter`, `zine`,
   `sunday`, `postcard`, `album`) so the pipeline vocabulary, the picker
   card ids, and the thumb template names all agree. A companion
   `BUILT_STYLES` frozenset (currently `{letter}`) records which members
   have a renderer:
   - `render_html` refuses unbuilt styles with a clear `HtmlRenderError`;
   - the CLI `--style` choice list is derived from `BUILT_STYLES`;
   - the web form's `Style` enum (in `web/pipeline.py`) lists built styles
     only, so it keeps doubling as the server-side gate;
   - the picker renders unbuilt cards with a SOON pill and a disabled
     radio (unchanged from the reskin).

Building a planned style is now: add
`templates/styles/<name>.html.j2`, add the member to `BUILT_STYLES`, flip
the card's `coming_soon` flag, add the member to the web `Style` enum, and
refresh the goldens (`make golden-update`). No prompt or eval changes, per
ADR-006.

## Consequences

- One built style means one golden, one visual-QA target, and a
  `make test-render` that renders one file until more styles land.
- The eval suite's register calibration is unaffected: the narrative voice
  formerly described as "the editorial register" is unchanged — it is The
  Letter's voice now.
- Anyone holding an old CLI invocation with `--style log` or
  `--style encyclopedia` gets a Click usage error; that is acceptable
  pre-release.
- ADR-006's *decision* (one narrative, many templates) stands; only its
  concrete style list is superseded. A status note points here.
