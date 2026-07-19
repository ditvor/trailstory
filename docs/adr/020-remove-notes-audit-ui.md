# ADR 020 — Remove the "Notes" audit UI from the memory page

**Date:** 2026-07
**Status:** Accepted (partially supersedes ADR-014's HTML layer)
**Decided by:** Igor Kochman

---

## Context

ADR-014 shipped sentence-level provenance in two layers: a data-model
layer (`NarrativeOutput.paragraphs` as `list[Paragraph]`, each sentence
carrying a `Provenance`) and an HTML layer — every sentence wrapped in
`<span class="sent" data-prov data-tip>`, plus a "Notes" toggle button
in all three style templates that flipped `body.audit` to reveal
per-source underlines, an amber tint on INFERRED sentences, and hover
tooltips.

The audit UI was already default-off after pilot users read the tints
as random highlighting. In practice the owner does not want the
underline treatment on the page at all: the memory page is a gift to
family, and even the opt-in author-facing audit chrome (the Notes
button, the underlines it reveals) works against the "reads as prose"
goal. The provenance *data* is still load-bearing elsewhere — the
ADR-011 verifier loop, the rubric's inferred-ratio ceiling, and the
faithfulness judge all consume it.

---

## Options considered

### Option A — Remove the whole HTML layer (button, audit CSS/JS, spans)

Strip the Notes button, the `body.audit` styles, the toggle script, and
the `data-prov` / `data-tip` attributes from all three style templates.
Sentences render as plain text. The data model is untouched.

**Pros:** page is pure prose; smaller output file; one less UI surface
to maintain across three styles; provenance pipeline and quality gates
unaffected.
**Cons:** the shipped HTML no longer carries grounding info for
inspection; an author who wants to audit must read the eval/rubric
output instead of the page.

### Option B — Keep the invisible `data-prov` spans, remove only the button and audit CSS

**Pros:** grounding info survives in the markup for tooling.
**Cons:** dead attributes bloat every sentence of a file whose size
already matters (base64 photos, sent over messengers); nothing consumes
them once the audit UI is gone.

---

## Decision

Option A. The page-side audit UI is removed entirely from
`templates/styles/editorial.html.j2`, `log.html.j2`, and
`encyclopedia.html.j2`. Sentence-level provenance remains a data-model
and eval concern only.

The writer prompt still asks for per-sentence provenance tags
(`llm/prompts.py`) — that contract feeds the ADR-011 verifier and the
rubric's inferred-ratio gate and is explicitly **not** removed. The
prompt's rationale sentence ("the reader's HTML page will surface this
on hover") is now stale; correcting it is a prompt tune and goes
through the eval flow separately.

---

## Consequences

- All three memory-page styles render sentences as plain text; no
  Notes button, no `body.audit`, no `data-prov` attributes.
- `NarrativeOutput` schema is unchanged (`schema_version` stays 5) —
  this is a rendering-only change.
- Goldens under `tests/golden/` regenerated via `make golden-update`.
- Re-adding an author-facing audit view should happen outside the
  shared page (e.g. a builder-only preview mode), not by reintroducing
  audit chrome into the gift artifact.
