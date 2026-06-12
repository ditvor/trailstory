# ADR 015 — Richer deterministic ledger fields

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

After ADRs 008–014 the narrative pipeline is structurally constrained:
the writer reads only a `FactLedger` (ADR-009), photos are pre-described
into a typed shape that flows into the ledger (ADR-010), and every
output sentence carries a provenance tag (ADR-014). Fabrication of
discrete facts has been mostly closed.

What remains is a different shape of drift. When the user complains
that the narrative "still sounds too poetic" and "drifts away from the
actual memory," part of the cause is that the ledger itself is thin.
The writer is told "ground everything in the ledger" but the ledger
gives it:

- `where` (one string),
- `season` (one string),
- a small chronology of beats (LLM-derived from the seed text and
  photo descriptions),
- a few GPX numbers (distance, elevation, duration, summit).

When the seed is short and photos are ambiguous, the writer has very
little material to anchor on and reaches for mood and atmospheric
prose to fill the space. That tendency lives in the model's training
and we've been trying to fight it from the wrong side — with stricter
prompts. The lever we haven't pulled is the ledger itself.

Auditing the inputs (see the chat thread that produced this PR), the
codebase is throwing away a lot of grounding signal:

1. **Per-photo GPS.** Every iPhone photo carries EXIF GPS. The resize
   pipeline strips it on save (correctly — privacy contract for the
   embedded base64 photo in the HTML output). But it strips before
   reading, so Python never sees the coordinates. A photo at km 5.4
   showing a lake is meaningfully different evidence from "a photo of
   a lake somewhere on the hike."
2. **Stops and dwell time.** Waypoint velocity drops to ~0 when the
   hiker stops. The track encodes every pause; we ignore them.
3. **GPX track name.** `extract_track_name()` already exists, and the
   builder UI uses it to populate the location chip. The ledger
   extractor never sees it.
4. **Sunrise/sunset.** The hike's relationship to daylight ("started
   in the morning, finished in the afternoon") is a real anchor the
   writer can lean on instead of guessing time-of-day from chronology
   beats.
5. **Track topology.** A loop, an out-and-back, and a point-to-point
   afford different narrative beats ("on the way back" vs "completing
   the circle"). The shape is implicit in the waypoints; we never
   classify it.

All of this can be computed from inputs we already have, without
external dependencies or extra LLM calls. The grounding gain is
structural: a denser ledger is a smaller fabrication surface.

---

## Options considered

### Option A — Add a storyboard stage between ledger and writer

The original proposal (see chat thread) included an explicit
storyboard layer: an extra LLM call that turns the ledger into an
ordered scene plan, which then becomes the writer's input. Three
stages instead of two: extractor → storyboard → writer.

**Pros:** clear separation between fact extraction and scene planning;
matches how humans write longer pieces.
**Cons:** adds a third LLM call and another point of failure for a
3–5 paragraph output; the `FactLedger.chronology` already encodes scene
order via beats; the rendering-time cost isn't justified by the size
of the output.

### Option B — Add LLM-extracted enrichment fields

Extend the extractor's prompt to also pull richer fields from the seed
(verbatim quotes, named landmarks, etc.) and let the LLM reason about
photo↔track correlation.

**Pros:** more semantic understanding; no new Python code.
**Cons:** asks the cheap Haiku extractor to do work it can't do well
(GPS correlation, sunrise math, velocity clustering); feeds numbers
through an LLM which invites transcription errors; the GPX-derived
facts under ADR-009 were deliberately kept Python-computed for exactly
this reason.

### Option C — Add deterministic fields, Python-computed

Compute the new facts in Python from the existing inputs:

- Read photo EXIF GPS into `PhotoMeta` before the strip-on-save step.
- Cluster low-velocity waypoints into `Pause` objects.
- Classify track shape (loop / out-and-back / point-to-point) from
  bounding-box vs total-distance geometry.
- Correlate each photo to its nearest waypoint (by GPS or timestamp).
- Compute total descent (gpxpy already exposes it).
- Read the GPX `<name>` tag into the ledger.
- Compute a daylight context string using `astral`.

Wire all of them into the deterministic half of `FactLedger`.

**Pros:** zero new LLM calls; deterministic; cheap; cumulative ledger
density; reuses the ADR-009 pattern (GPX numbers and season are
already Python-computed); composes with the existing fabrication
constraints because everything goes through the same single-input
contract for the writer.
**Cons:** adds one dependency (`astral`); the photo↔track correlation
strategy has two fallbacks (GPS vs timestamp) and the timestamp path
is best-effort due to EXIF / GPX timezone ambiguity; new fields need a
small prompt hint so the writer knows they exist.

### Option D — Status quo + tighter prompt

Leave the ledger shape as-is; try to fix drift with more aggressive
writer-prompt rules and more goldens.

**Pros:** no schema or pipeline change.
**Cons:** voice work is asymptotic; the prompt has been tightened
multiple times already (see the dated SYSTEM_NARRATIVE revisions in
`llm/prompts.py`); we're hitting the ceiling of what prompt edits can
do without giving the writer more material to anchor on.

---

## Decision

**Option C.** Expand the deterministic half of `FactLedger` with seven
new fields, all computed in Python from the existing inputs:

| Field | Source |
|---|---|
| `track_name` | GPX `<name>` tag (already extracted, now wired through) |
| `track_shape` | Bounding-box-vs-distance classifier in `trailstory.gpx` |
| `elevation_loss_m` | `gpxpy.get_uphill_downhill().downhill` |
| `day_of_week` | `when.strftime("%A")` |
| `daylight_context` | `astral` sunrise/sunset bucketed into pre-dawn / dawn / morning / midday / afternoon / dusk / evening |
| `pauses` | Velocity clusters where instantaneous velocity < 0.3 m/s for ≥ 5 min |
| `photo_positions` | Per-photo `(km_along_track, ele_m)` via photo GPS or timestamp fallback |

The photo GPS read happens *before* the strip-on-save step in
`load_photos`, so the output JPEG still has its GPS sub-IFD stripped —
the privacy contract for the embedded base64 photo is unchanged. The
coordinates only travel through `PhotoMeta.gps_lat` / `gps_lon` into
the ledger.

The writer prompt gains a single new bullet naming these fields and
forbidding the writer to invent the structural facts they encode
(pauses, positions, topology). Voice work is not part of this PR.

`NarrativeOutput.schema_version` is bumped from 3 to 4 so cached
narratives produced under the old (thinner) ledger are invalidated.

The rubric (`tests/eval/rubric.py`) gains three style metrics — a
banned-substring gate per language, an average-sentence-length band
check, and an inferred-ratio ceiling — so the next change in this
direction (the voice tightening planned for PR 2) has a measurement
layer to land into.

---

## Consequences

**What becomes easier:**

- The writer can reference real positions ("around the 4 km mark", "at
  the saddle") instead of inventing them.
- A pause-related beat is now grounded in actual track data — "we
  stopped by the river to eat" can point at a real 23-minute velocity
  drop rather than being a writer's plausible-sounding embellishment.
- Time-of-day language is anchored to actual sunrise/sunset for the
  hike's date and location, not inferred from chronology vibes.
- Track topology constrains framing: an out-and-back affords "on the
  way back" without the writer having to guess whether the track
  returned to the start.
- The eval has measurement for voice metrics (sentence length, banned
  phrases, inferred ratio) so the upcoming PR-2 voice tightening can
  be evaluated objectively.

**What becomes harder:**

- One new third-party dependency (`astral`, MIT-licensed, local
  computation only). No new external API call.
- Tests that constructed `NarrativeOutput` with `schema_version=3`
  needed bulk-updating to v4 (mechanical change; done).
- The bumped `NarrativeOutput.schema_version` invalidates every user's
  existing narrative cache. Acceptable: the cached narratives don't
  reflect the richer ledger and re-running on cache miss is the right
  behaviour.

**Privacy:**

- Photo GPS is read into Python (`PhotoMeta.gps_lat`, `gps_lon`) and
  flows into the ledger (`FactLedger.photo_positions`, with derived
  `km_along_track` not raw coordinates). The output JPEG embedded in
  the HTML page still has its GPS sub-IFD stripped. The new fields
  are part of the LLM prompt and so are visible to the model
  provider, same as every other piece of hike data.
- No external geolocation API is called — `astral` runs locally.

**Follow-ups:**

- PR 2 (voice tightening + verbatim user phrases): the next phase
  uses the measurement layer added here.
- ADR-016 (planned): writer voice tightening + verbatim user phrase
  anchor in the extractor.
- The banned-phrase lists for RU and DE are currently empty; the user
  will populate them after observing eval output. The rubric gracefully
  passes ("no banned phrases configured") when the list is empty.
- Pause detection assumes dense-enough waypoints (sub-minute
  intervals). Manually-edited GPX with sparse points won't surface
  pauses; this is documented in `_detect_pauses` and treated as fail-quiet.
- Photo↔track correlation falls back to timestamp matching when GPS
  is absent. EXIF `DateTimeOriginal` is naive **local wall-clock**
  while GPX timestamps are UTC, so a raw naive-to-naive comparison is
  systematically off by the timezone offset — including (especially)
  for same-device sessions: the same iPhone writes local time into
  EXIF and UTC into the GPX. Two mitigations: (1) GPS-bearing photos
  in the same upload double as clock anchors — the median of
  (matched-waypoint time − photo EXIF time) calibrates the offset
  applied to GPS-less photos; (2) any match whose best time delta
  exceeds 30 minutes is omitted rather than guessed. An upload with
  no GPS-bearing photos and a non-UTC camera clock therefore usually
  yields no timestamp matches — by design, the omit-rather-than-guess
  policy. (An earlier draft of this ADR claimed same-device sessions
  align under raw comparison; that was wrong and is corrected here.)
- `Pause.at_km` and `PhotoPosition.km_along_track` derive from
  cumulative point-to-point distance, which includes stationary GPS
  jitter that `distance_km`'s moving-distance basis excludes. Both
  are clamped to `distance_km` so the ledger never reports a position
  beyond the hike's own length (divergence is bounded by roughly
  stopped-time × 1 km/h — a few percent on pause-heavy tracks).
