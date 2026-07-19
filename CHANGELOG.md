# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed
- **Style lineup renamed and pruned (ADR-021).** The `editorial` style is
  now `letter` ("The Letter") end-to-end: enum value, template
  (`templates/styles/letter.html.j2`), fonts directory and embedded
  font-family names (`Letter Serif` / `Letter Mono`), body marker class
  (`style-letter`), web form value, and goldens. The `Style` enum now
  carries the full five-style product lineup from the Claude Design
  proposal (`letter`, `zine`, `sunday`, `postcard`, `album`); a new
  `BUILT_STYLES` set (currently `{letter}`) gates the renderer, the CLI
  `--style` choices, and — via the narrower `web.pipeline.Style` enum —
  the builder form, so the four planned styles stay visible as SOON
  cards but cannot be submitted or rendered until their templates land.
- **Web favicon.** Replaced the boot-emoji tab icon with an ink elevation-line
  mark on warm paper, matching the builder's "Letter" palette. Pure inline SVG
  data URI in both base templates — no asset files.

### Removed
- **`log` and `encyclopedia` styles (ADR-021).** Templates, enum values,
  goldens, and style-specific tests deleted. They matched none of the
  five picker cards' visual promises, were already hidden from the web
  picker, and were never surfaced to a real user.

### Added
- **The Postcard Set style (`--style postcard`).** Third built style
  from the ADR-021 lineup: mid-century travel cards laid out on a
  kraft desk. The cover is a photo front with the title on its caption
  band; each paragraph becomes a front/back postcard pair — the
  chapter photo as a bordered, sun-faded front, the paragraph as a
  handwritten message on the back beside a Trailstory postage stamp
  (summit elevation as the denomination), a wavy-line postmark
  (location + date), and a "To: home" address block. Pull quote as an
  air-mail banner card; stats as an itinerary card + vintage route-map
  card; overflow photos in "the rest of the set". Tri-lingual
  (EN/RU/DE) and fully self-contained (ADR-001): Yeseva One (display)
  and Caveat (handwriting) latin+cyrillic subsets are committed under
  `templates/fonts/postcard/` — both chosen over Latin-only
  "Greetings from" faces because RU is a primary audience — and
  JetBrains Mono is reused from the Letter set as the postal small
  print. `postcard` joins `BUILT_STYLES`, the CLI `--style` choices,
  and the web builder picker (its card loses the SOON pill).
- **The Zine style (`--style zine`).** Second built style from the
  ADR-021 lineup: a riso-printed indie-zine treatment of the same
  narrative — ink + terracotta spot color on warm paper, real SVG
  duotone halftone photos with tape-down framing, a massive
  solid/outlined condensed cover title, numbered paragraph sections,
  a route badge, a "filed from" postmark stamp, and a rotated
  terracotta pull-quote block. Tri-lingual (EN/RU/DE) like The Letter,
  fully self-contained (ADR-001): Oswald latin+cyrillic subsets are
  committed under `templates/fonts/zine/` (Big Shoulders has no
  Cyrillic; Oswald is the condensed stand-in) and JetBrains Mono is
  reused from the Letter set as the typewriter body. `zine` joins
  `BUILT_STYLES`, the CLI `--style` choices, and the web builder
  picker (its card loses the SOON pill).
- **Back link on the generated memory page.** All built styles render a
  tri-lingual "← Back to the main page" link below the share row. It ships
  `hidden` and is revealed by a small script only when the page is served
  by the web builder (`http(s)` + `/memory/…` path) — a saved or forwarded
  copy of the file never shows it, keeping the shareable HTML clean for
  recipients. Excluded from print.
- **"Save as PDF" and "Share page (HTML file)" on the memory page.** The
  share row in all three styles now offers a PDF export (the browser's
  print dialog against a cleaned-up print stylesheet — chrome hidden,
  current language only) and an explicit way to share the page as the
  self-contained `.html` file it is: the native share sheet on phones
  (WhatsApp, Telegram, email — recipient's choice), a plain download
  elsewhere. Log and encyclopedia gain the print stylesheet rules that
  editorial already had.
- **POI name resolution (ADR-019).** Opt-in (`--poi`, implies `--place`)
  resolution of a hiker's generic landmark beat ("the wax-figure church") to
  a real named OpenStreetMap feature ("Mühlfeldkirche"), fed into the place
  stitch as a grounded fact. Deterministic — the name comes from OSM, never
  the LLM: new `trailstory/poi.py` queries Overpass for named landmark
  features (places of worship, peaks, lakes, huts, castles, waterfalls,
  viewpoints, monasteries) near the track, categorises each hiker beat by
  keyword (EN + DE), and matches **only when exactly one** named feature of
  that category is near the route — a wrong name is worse than a generic one,
  so it fails closed. `PoiMatch` model + `PlaceContext.named_landmarks`; the
  stitch prompt gains a POI input; all three styles credit OpenStreetMap
  (ODbL) when a landmark is named. Stdlib `urllib`, no new dependency. Web
  wiring deferred. Live validation: the church label now requires
  `religion=christian` (a Bad Tölz mosque was being mislabelled), and the
  church-dense town correctly yields no match.
- **Place-stitch eval net (ADR-017 follow-up).** A programmatic rubric for
  the "about this place" stitch (`tests/eval/place_rubric.py`): tri-lingual
  presence, per-language length band, town-named-in-EN, Russian-actually-
  Cyrillic, the shared ADR-016 banned-phrase gate, `used_hiker_details` are
  real beats (faithfulness), and an EN content-word grounding ratio against
  the supplied extract + beats. Unit-tested for free in `make ci`
  (`tests/test_eval_place_rubric.py`) — the always-on net — plus a paid
  runner over fixed cases (`make eval-place` / `python -m tests.eval.run_place`).
  The net immediately caught the Haiku stitch reaching for the banned
  "the kind of" construction and over-editorialising with ungrounded
  filler, so the place-stitch model default moves to
  `claude-sonnet-4-6` (override via `PLACE_MODEL`).

### Removed
- **`examples/memory-herzogstand.html`.** A hand-crafted design mockup from
  the initial scaffold, never produced by the pipeline: EN/RU toggle only
  (no German, predates ADR-005), Google Fonts loaded from CDN (breaking the
  self-contained rule), no embedded photos, and the pre-#73 "Copy link" /
  WhatsApp share row. Its source inputs were never committed, so it could
  not be re-rendered; nothing referenced it. Current-design fixture renders
  live in `tests/golden/test-render-<style>.html`.
- **"Copy link" and WhatsApp buttons on the memory page.** Both shared
  only a title-plus-quote text snippet — there is no link to copy on a
  self-contained page, and the WhatsApp button never carried the memory
  itself. Superseded by the HTML-file share above.
- **"Notes" audit UI on the memory page (ADR-020).** The per-sentence
  provenance underlines, tints, hover tooltips, and the Notes toggle
  button are gone from all three styles — the page now reads as plain
  prose with no audit chrome. Rendering-only: sentence-level provenance
  stays in the data model (`schema_version` unchanged) and keeps feeding
  the ADR-011 verifier and the rubric's inferred-ratio gate. Partially
  supersedes ADR-014's HTML layer.

### Changed
- **Builder docket now says "family abroad, friends elsewhere"** (was
  "family in Russia, friends elsewhere") in all three languages — the
  audience framing is broader than one country.
- **`Settings.place_model` default is now `claude-sonnet-4-6`** (was
  `claude-haiku-4-5`). See the eval-net entry above — Haiku failed the
  place voice + grounding gates; Sonnet passes them. Same reasoning as the
  ADR-018 vision bump.
- **"About this place" in the web builder (ADR-017 follow-up).** The
  opt-in place block is now reachable from the hosted app, not just the
  CLI: an off-by-default checkbox on the builder form carries the toggle
  through the streaming pipeline's pending state, `stream_pipeline`
  resolves the block after the narrative (reusing the ledger it already
  extracted — `generate_narrative_stream` gains a `ledger=` param), and a
  `place_client_factory` plus an injectable geocode/Wikipedia resolver are
  wired through `create_app`. The fake-LLM dev mode (`--fake-llm`) ships an
  offline stub resolver + fake stitch client so the block renders without
  any network call.
- **Web builder reskin → "The Letter" (compose).** The builder page
  (`web/templates/landing.html.j2` + `builder_base.html.j2` +
  `web/static/builder.css`) moves from the "Notebook" workshop look to a
  warm correspondence treatment that mirrors the editorial output page:
  a to/from docket (FROM fills in with the detected place), a "Keep the
  day. / Send it home." serif hero, sections reordered to *photos → how
  it felt → from the walk → choose how to tell it*, a PAR AVION stamp at
  the send, and a "with love, from the trail" sign-off. The opt-in
  "about this place" checkbox is preserved, restyled to the new theme.
  Selecting a style warms the page chrome toward that style's palette via
  `--c-*` "chameleon" tokens (wired on selection; in v0 only The Letter is
  buildable, so it resolves to the editorial palette and is ready for the
  moment more styles unlock). Same FastAPI backend, Alpine wiring, and
  `/generate` flow — no React, no build step. Fonts stay self-hosted
  (Source Serif 4 with Latin **and** Cyrillic subsets, Onest, JetBrains
  Mono, Caveat) — no Google Fonts CDN, so the builder makes no third-party
  request, matching the privacy stance. The pre-submit copy claim is now
  accurate ("we don't keep your photos"). Output memory styles (editorial /
  log / encyclopedia) are untouched. Also fixes a latent bug: the track
  minimap's start/finish markers used a `<template x-if>` inside an `<svg>`
  (Alpine can't `cloneNode` an SVG `<template>`), so they never rendered —
  now an `x-show` `<g>` with guarded coordinates.
- **Enriched photo description (ADR-018).** The per-photo vision pass
  now produces four new `PhotoDescription` fields: `interactions` (how
  people carry/relate, e.g. "an adult wearing a child carrier"),
  `legible_text` (signs/markers transcribed verbatim — corroborates the
  GPX location / route name), `scene_type`, and `light_and_color`.
  `interactions` is **orientation-free by contract**: the describer
  prompt forbids front/back/chest/hip, and `photos._scrub_orientation`
  strips any that slip through while keeping the grounded carry fact. A
  matching writer hard rule forbids asserting a carry / worn-object
  orientation absent from the ledger. Closes the "baby carrier on the
  back" fabrication — a spike on real photos showed every vision model
  guesses orientation unreliably even when told not to.
- **"About this place" block (ADR-017).** An optional, opt-in
  (`--place`) block that gives the reader a short, warm sense of where
  the hike happened. Facts are *sourced, not generated*: the hike's GPS
  coordinates are reverse-geocoded (OpenStreetMap Nominatim) to a town +
  region, the town's Wikipedia summary supplies a grounded reference
  extract, and a dedicated LLM "stitch" call weaves that extract together
  with the hiker's own ledger beats (the church they passed, the lake) —
  forbidden from adding any fact not in one of those two sources. New
  `trailstory/place.py` (deterministic fetch, stdlib `urllib`, no new
  dependency), `trailstory/llm/place.py` (the stitch), `PlaceContext` on
  `Memory`, `Settings.place_model` / `use_place_context`, and a place block
  in all three visual styles (editorial / log / encyclopedia) with CC BY-SA
  source attribution. The hiker's `--location` wins over the geocoded town
  (a track midpoint can fall in a neighbouring municipality); reverse-geocode
  only fills the region. Off by default (reverse-geocoding discloses
  coordinates). Soft-fails everywhere: a missing article yields a town-only
  line, a failed geocode or stitch omits the block. Live-validated against
  real Bad Tölz data. The web builder is not yet wired.
- **Writer voice tightening + verbatim user phrase anchor (ADR-016).**
  The writer prompt now positions the register explicitly ("a warm
  family note to grandparents — neither a travel essay nor minutes of
  a meeting"), carries four binding voice rules (no personified
  landscape, no feeling/atmosphere sentence subjects, ≤ 2 adjectives
  per noun phrase, no "the kind of" construction family), and shows
  five BAD/GOOD pairs whose BAD halves are real sentences from the
  2026-06 golden refresh. The `FactLedger` gains
  `verbatim_user_phrases`: 2–4 short quotes (≤ 8 words) the extractor
  copies from the seed text, re-verified in Python as genuine literal
  substrings; the writer must weave at least one into the prose —
  verbatim in the language the hiker wrote it in, rendered faithfully
  in the other two. A free verifier (ADR-011 pattern) regenerates once
  when no phrase surfaced, keeping the regen only if it improves.
- **Richer deterministic ledger (ADR-015).** The `FactLedger` the
  writer consumes now carries seven new Python-computed fields:
  `track_name` (from the GPX `<name>` tag), `track_shape`
  (loop / out-and-back / point-to-point), `elevation_loss_m`,
  `day_of_week`, `daylight_context` (sunrise/sunset buckets via the
  new `astral` dependency — local computation, no external API),
  `pauses` (rest stops ≥ 5 min detected from waypoint velocity
  clustering), and `photo_positions` (each photo's km-along-track via
  EXIF GPS or clock-calibrated timestamp matching). Denser ledger →
  less room for the writer to invent specifics. Photo EXIF GPS is
  read into `PhotoMeta` *before* the strip-on-save step; the output
  JPEG still has GPS stripped, so the privacy contract for the
  shareable HTML file is unchanged.
- **Style metrics in the eval rubric.** Banned-substring gates per
  language (EN list seeded with the PR56 "travel-essay" tics; RU/DE
  to be populated from eval observation), an average-sentence-length
  band (6–24 words), and an inferred-ratio ceiling (0.6) — the
  measurement layer for the upcoming writer-voice tightening. A free
  CI test scans committed goldens for banned phrases; it arms
  automatically once goldens are refreshed to schema v4.

### Fixed
- **`examples/wax_saints/render.py` runs again.** The demo script still
  built `NarrativeOutput` with the pre-ADR-014 flat `LocalizedParagraphs`
  shape and crashed against the current model. It now builds sentence-level
  paragraphs (9 sentences across 4 paragraphs) with per-sentence
  provenance tags, so the rendered page also demonstrates the editorial
  Notes/audit hover correctly. Verified end-to-end with the repo venv.

### Changed
- **Vision describer model default → `claude-sonnet-4-6` (ADR-018).**
  The ADR-018 spike showed Haiku misreads the fine detail the enriched
  describer fields depend on (it called a child carrier a "dog");
  Sonnet reads it correctly. Overridable via `VISION_MODEL` for
  cost-sensitive batch runs. Existing Haiku-keyed vision-cache entries
  invalidate cleanly (different model component in the key).
- **`NarrativeOutput.schema_version` bumped 4 → 5 (ADR-016).** Output
  shape unchanged, but the writer prompt's register and the new
  verbatim-phrase contract mean cached v4 narratives no longer reflect
  what the current pipeline produces. Narrative-cache entries
  invalidate on first read; goldens require a refresh
  (`make eval-update-golden`), which also re-arms the banned-phrase
  golden gate.
- **`NarrativeOutput.schema_version` bumped 3 → 4.** The output shape
  is unchanged, but the writer prompt now references the ADR-015
  ledger fields, so cached v3 narratives no longer reflect what the
  current pipeline produces. All existing narrative-cache entries
  invalidate on first read; goldens require a refresh
  (`make eval-update-golden`).
- **Editorial photo layout: consistent aspect ratio + interleaved
  through paragraphs.** `templates/styles/editorial.html.j2` now locks
  every `.figure img` to a `3 / 2` aspect ratio with
  `object-fit: cover`, so portrait and landscape originals render as
  the same rectangle instead of a ragged mixed-height stack. Photos
  past the hero are interleaved one-per-paragraph through the body
  (full-column variants `v-b` / `v-c` / `v-d`) instead of the previous
  "one in the middle, all the rest stacked after the pull quote"
  pattern; any overflow falls after the quote with the original
  rotation. Pilot users on 6–8-photo hikes were reading the old layout
  as a top half of prose followed by a bottom half of photo dump;
  interleaving keeps the visual rhythm matched to the prose rhythm.
- **Per-sentence provenance UI hidden by default; `Notes` audit toggle
  added (editorial style).** The ADR-014 INFERRED tint and the
  per-sentence `title=` tooltip used to be on for every reader; pilot
  users read the amber background as random highlighting and the
  resulting `cursor: help` as a broken affordance (the native browser
  tooltip is slow, low-contrast, and absent on touch). The audit UI
  now lives behind a `Notes` toggle in the editorial template's top
  bar, which flips `body.audit` and reveals a richer treatment than
  before: per-source colour cues (INFERRED amber background; PHOTO /
  SEED / GPX underline tints) plus a custom `::after` tooltip reading
  `data-tip` so it actually renders quickly on hover with readable
  contrast. The `<span class="sent">` carries `data-prov` + `data-tip`
  on every sentence regardless, so the data is still available; the
  default rendering just stays clean for the page's actual audience
  (a family member, not the author). Author preference is persisted
  in `localStorage` under `trailstory.notes`. Log and encyclopedia
  styles already used the flat fallback and are unaffected.
- **Writer prompt rebalanced for warmth + faithfulness.**
  `SYSTEM_NARRATIVE` and `USER_NARRATIVE_TEMPLATE` in
  `trailstory/llm/prompts.py` no longer ask for an "intimate, literary"
  voice / "Bourdain on a quiet afternoon" framing — pilot output
  drifted into ornate atmospheric prose detached from the hiker's
  seed text. The prompt now frames the task as "a letter home to
  people who love them — warm, intimate, direct", with explicit
  instructions to name the people from the ledger when they appear
  in a beat, surface the sensory specifics (light, sound, smell,
  texture) and emotions the ledger records, and avoid the magazine
  essay register. Grounded-sentence aim raised from ≥ 60% to ≥ 70%
  of `seed` / `photo` / `gpx` provenance. New hard rule: do not
  quote GPX numbers (distance, elevation, duration, summit height)
  verbatim in the prose — those live in the stats block, qualitative
  reference only. Milestone JSON skeleton now states the
  "under 30 characters in every language" rubric ceiling explicitly
  so the model stops blowing the cap on RU/DE. Paragraph count
  remains 3–5 (a first iteration shortening it to 2–3 was rejected
  by the eval). Previous prompt versions preserved as dated comments
  for revertability. CLI narrative cache is not invalidated by
  prompt-only changes — clear `~/.cache/trailstory/narratives/` to
  regenerate old hikes with the new register; the web builder
  streaming path bypasses cache and is unaffected.

  **Eval status (paid LLM-as-judge, run on PR branch before merge,
  threshold 1.00):** all 4 cases pass judge regression after two
  prompt iterations. Cases 01/02/03/04 warmth Δ = -0.5, +0.5, 0.0,
  -0.5; narrative_arc Δ = -0.5, +0.5, 0.0, -0.5; russian_fidelity
  Δ = -0.5, 0.0, 0.0, 0.0; faithfulness Δ = +0.12, -0.24, +0.38,
  +0.90 (faithfulness improved in three cases, dipped marginally on
  case 02 within threshold). Goldens refreshed via
  `make eval-update-golden` and committed alongside the prompt.
- **Two-pass narrative pipeline with a structured `FactLedger` (Phase 2 of
  the narrative-faithfulness initiative;
  [ADR-009](docs/adr/009-two-pass-narrative-with-fact-ledger.md)).** Narrative
  generation is now a two-LLM-call flow: a cheap Haiku-class extractor
  reads the seed + GPX + photo timestamps and emits a structured
  `FactLedger` (people, weather, chronology beats with
  `objects_mentioned`); the Opus writer consumes the serialized ledger as
  its sole input — no raw seed text reaches it. The writer is therefore
  structurally unable to introduce a duck if the ledger contains no
  duck; the prompt's anti-fabrication clause now enforces grounding
  against the ledger's typed shape, not against free-form prose. New
  `Person`, `Beat`, `FactLedger` Pydantic models in `trailstory/models.py`;
  new `extract_ledger()` public function and `LedgerExtractionError` in
  `trailstory/llm/narrative.py`; new `SYSTEM_LEDGER_EXTRACTOR` +
  `USER_LEDGER_EXTRACTOR_TEMPLATE` prompts; new `Settings.ledger_model`
  (default `claude-haiku-4-5`). The CLI, the web pipeline, and the eval
  runner all construct two `AnthropicClient` instances per generate run;
  `web.create_app` gains a parallel `ledger_client_factory` parameter
  for tests + fake-LLM dev mode. Previous ADR-008 writer prompt
  preserved as a dated comment for revertability. Renderers
  (`render_html`, `render_instagram_carousel`) are unchanged — they
  consume `Memory`, not the ledger.
- **Writer prompt grounded in date + season; anti-fabrication clause added
  (Phase 1 of the narrative-faithfulness initiative;
  [ADR-008](docs/adr/008-writer-prompt-temporal-grounding-and-anti-fabrication.md)).**
  `USER_NARRATIVE_TEMPLATE` now receives `{hike_date}` and `{season}`
  placeholders, populated by a new `_infer_date_and_season(gpx_stats)`
  helper in `trailstory/llm/narrative.py` that walks waypoints for the
  first non-None timestamp and picks hemisphere from latitude (Apr in
  Bavaria → "spring (April; northern hemisphere)"; Apr in Patagonia →
  "autumn (April; southern hemisphere)"). The "Write the memory…"
  paragraph extended with an explicit anti-fabrication clause that
  enumerates concrete forbidden examples (ducks, chopsticks, named
  objects) and permits generic nature words. Previous prompt preserved
  as a dated comment per CLAUDE.md convention. Target: lift average
  faithfulness ≥ 0.5 from the 1.32 / 5 Phase 0 baseline. CLI cache
  intentionally not invalidated (see ADR-008 known limitations); web
  builder streaming path is unaffected since it bypasses cache.
- **Builder UI redesign — single-page builder, editorial design system.**
  The builder (`web/`) now uses the same `Editorial Serif` /
  `Editorial Mono` design system as the rendered memory page, served
  from `web/static/builder.css` and `web/static/fonts/`. Layout
  follows the Claude Design proposal: 760px column, sticky header
  with logo and EN/RU/DE toggle, mono eyebrow + serif display hero,
  four numbered sections (`01` track, `02` photos, `03` description,
  `04` style), bespoke drop zones, client-side photo preview grid
  (Alpine + `URL.createObjectURL`), description textarea with word
  counter, optional location text input, 5-card style picker, and a
  generate CTA. The flow stays single-page: everything posts to
  `POST /generate` in one shot. Builder UI is tri-lingual EN/RU/DE —
  all three languages bake into the page, a CSS attribute selector
  hides the inactive two, and a small Alpine root persists language
  choice to `localStorage`. The audience promise from ADR-005 now
  applies to the chrome, not just the rendered memory.
- **Style picker matches the design's five names.** The cards are
  `The Letter` (Editorial · magazine essay), `The Zine` (Riso ·
  two-color · loud), `Sunday` (Joyful · warm cream + coral),
  `Postcard Set` (Vintage travel · seven cards), and `Album`
  (Scrapbook · polaroid + tape). Only `The Letter` is buildable in
  v0 — it maps to the existing `editorial` renderer. The other four
  carry SOON pills and `disabled` radios; their renderers haven't
  been built yet. The legacy `log` and `encyclopedia` renderers stay
  in the codebase (and accept direct `POST /generate` submissions)
  but are intentionally not surfaced in the picker because their
  visual treatment doesn't match what `The Zine` / `Sunday` promise.

### Added
- **Phases 2.5 + 3.1 + 3.2 + 4 of the narrative-faithfulness
  initiative, bundled.** Four ADRs land together because each builds
  on Phase 4's sentence-level provenance schema:
  - **[ADR-011](docs/adr/011-verifier-loop-self-reported-provenance.md) (Phase 2.5)** —
    Verifier loop using self-reported provenance. After the writer
    pass returns, count the INFERRED-sentence share; if it exceeds
    `Settings.max_inferred_ratio` (default `0.5`), regenerate once
    with feedback. Free signal (no extra LLM call to detect);
    "improvement only" admission policy keeps the regen only if its
    ratio improved on the original. Streaming bypassed.
  - **[ADR-012](docs/adr/012-per-photo-vision-cache.md) (Phase 3.1)** —
    On-disk per-photo cache for vision descriptions, keyed by
    `(photo bytes SHA-256, vision_model)`. Mirrors the existing
    narrative cache pattern; lives in
    `~/.cache/trailstory/vision/`. CLI honours the existing
    `--no-cache` flag; eval pins `use_cache=False`.
  - **[ADR-013](docs/adr/013-parallel-vision-via-threadpool.md) (Phase 3.2)** —
    `describe_photos` parallelises vision calls via
    `ThreadPoolExecutor`. `Settings.vision_concurrency` (default
    `4`) caps concurrency. 6-photo hike drops from ~6s to ~1.5s
    wall time. Order preserved via `.map`; single-photo fast path
    skips the pool.
  - **[ADR-014](docs/adr/014-sentence-level-provenance-and-html-hover.md) (Phase 4)** —
    Sentence-level provenance. New `ProvenanceSource` enum, new
    `Provenance` + `Sentence` models, `NarrativeOutput.paragraphs`
    becomes `list[Paragraph]` where `Paragraph = list[Sentence]`.
    Each sentence carries tri-lingual text + one provenance tag.
    Writer prompt rewritten to produce + tag sentences. Editorial
    HTML template wraps each sentence in `<span class="sent"
    data-prov="...">` with title hover + subtle tint on INFERRED.
    Log and Encyclopedia templates use the new
    `paragraphs_as_localized()` helper to render unchanged until
    Phase 4.1 ports them. `schema_version` bumps to `3`; all
    goldens refreshed.
- **Multimodal photo grounding via Claude vision (Phase 3 of the
  narrative-faithfulness initiative;
  [ADR-010](docs/adr/010-photo-grounding-via-vision.md)).** Per-photo
  vision describer pass: each photo is sent through a cheap Haiku
  vision call (`Settings.vision_model`, default `claude-haiku-4-5`) and
  validated as a typed `PhotoDescription` (people visible, objects
  visible, location clues, season clues, body-language notes). The
  ledger extractor receives these via a new `{photo_descriptions_json}`
  placeholder; the writer's chronology beats can now reference
  photo-grounded facts (a baby's hat colour, a lake in the background)
  the seed text never bothered to name. Hard rules from ADR-009 still
  apply: the writer can only reference what is in the ledger; vision
  enriches the ledger, it does not bypass the constraint. `Settings.use_photo_grounding`
  (default `True`) is the master switch — set to `False` for
  cost-sensitive batch runs or to bisect a quality regression to the
  vision pass. Per-photo failures soft-fail (logged + skipped) so a
  flaky vision call doesn't tank a whole render. New `complete_vision()`
  method on `AnthropicClient`; new `describe_photo` / `describe_photos`
  in `trailstory/photos.py`; new third client factory plumbing in
  `web.app` + `web/__main__.py` + `tests/eval/run.py`. Renderers
  unchanged.
- **Faithfulness eval axis (Phase 0 of the narrative-faithfulness
  initiative; [ADR-007](docs/adr/007-faithfulness-eval-axis.md)).** The
  paid LLM judge now extracts every concrete factual claim from the
  English narrative and labels each `SUPPORTED` / `INFERRED` /
  `UNSUPPORTED` with a source quote. A derived `faithfulness` score
  (`@computed_field` on `JudgeScore`, same 0-5 scale as the other axes)
  is added to `JUDGE_AXES` and the regression gate. New types
  `FaithfulnessVerdict` and `ClaimVerdict` live in `tests/eval/judge.py`;
  the rubric paragraph and JSON skeleton in
  `tests/eval/judge_prompts.py` get one new section. Existing
  pre-faithfulness goldens validate cleanly (default empty
  `claim_verdicts` → `faithfulness == 0.0`) until refreshed by
  `make eval-update-golden`. No production code paths touched — this is
  measurement infrastructure for the upcoming Phase 1 prompt-only fixes
  and Phase 2 fact-ledger architecture.
- **`web/static/builder.css`** — full design-system bundle for the
  builder: paper/ink/rule oklch tokens, drop zones, populated
  track-loaded card, photo grid, AUTO-EXTRACTED chips (inline-edit
  state), style picker, live SSE draft block, generate CTA, sticky
  header, language toggle, responsive breakpoints for ≤720px.
  Style-card thumbnails are small CSS-only Jinja partials under
  `web/templates/style_thumbs/` (one per design card).
- **Live preview endpoints** for the builder's "drop and see"
  experience. `POST /preview/gpx` parses a GPX file in-memory (round
  trips through a tmp file the request unlinks before returning) and
  returns filename + point count + distance / ascent / time / summit /
  detected-location + an SVG ``d`` path for the mini-route. `POST
  /preview/photo` reads EXIF `DateTimeOriginal` from a single photo
  in-memory and returns its ISO date. Both endpoints are read-only:
  no workspace is created, nothing is persisted, the rate limiter
  isn't touched — they exist purely to populate the track-loaded
  card and the AUTO-EXTRACTED date chip the moment the user picks a
  file.
- **AUTO-EXTRACTED chips** below the description textarea: 📍
  location (editable inline; pre-filled from GPX track name when
  available) and ◷ date (read-only display; photo EXIF wins over
  GPX waypoint time when both are present). Each chip carries a
  source label that reflects the actual origin ("from track" /
  "from photo EXIF" / "you typed").
- **`web/copy.py`** — tri-lingual builder UI metadata: `STYLE_CARDS`
  display table (name/sub/desc per language, `coming_soon` flag),
  `accepted_style_values()` helper, `resolve_lang()` query-param
  coercion, `SUPPORTED_LANGS`.
- **`trailstory.gpx.extract_track_name`** — pulls the file-level
  `<name>` or first `<trk><name>` from a GPX file. Clipped to 120
  chars and trimmed; returns ``None`` on parse failure or when no
  name is present.
- **`trailstory.photos.read_exif_date`** — reads EXIF
  DateTimeOriginal / Digitized / DateTime from raw photo bytes,
  without falling back to file mtime. Used by the `/preview/photo`
  endpoint so the date chip's "from photo EXIF" provenance is
  honest.
- **Self-hosted builder fonts** at `web/static/fonts/` — the same
  variable Source Serif 4 + JetBrains Mono pair the editorial output
  renderer uses, with latin / cyrillic subset split.

### Changed
- **Editorial style — magazine-grade redesign.** Same `editorial` Style
  enum value, same renderer entrypoint, same `NarrativeOutput` contract;
  the template is rewritten head-to-toe. New visual identity: oklch
  paper/ink tokens (no accent colors, no gradients, no rounded corners),
  Source Serif 4 italic-top / roman-bottom display title, drop cap on
  the first paragraph, two-column desktop grid with marginalia sidebar
  (THE FACTS / THE PATH / ELEVATION), mono "eyebrow" labels, hairline
  rules, photo float-right + mid-body full-bleed + after-quote variants,
  reading-progress bar, 3-way EN/RU/DE toggle with keyboard shortcuts
  (`←/→` cycles language, `J/K` jumps paragraph, `?` opens a
  cheatsheet), `localStorage` language persistence, soft fade swap with
  paragraph-anchored scroll preservation, photo blur-up on load, print
  rules, `::selection` style. The `log` and `encyclopedia` styles are
  untouched.
- **Marginalia is statically positioned** in the editorial style (no
  `position: sticky`). Sticky behaviour interacts badly with headless
  PDF capture (Puppeteer, wkhtmltopdf) and with the user's primary
  share path, which is "save the page, send the file." Static keeps
  print-to-PDF and any headless renderer producing the same layout the
  user sees on screen.

### Added
- **Editorial fonts embedded as base64 WOFF2** under
  `templates/fonts/editorial/`. Six subsets — Source Serif 4 italic +
  roman variable axes × latin + cyrillic, plus JetBrains Mono variable
  × latin + cyrillic, ~476 KB on disk. Loaded into the template context
  by a new `_editorial_fonts()` helper in
  `trailstory.renderers.html` (memoised with `lru_cache`, only invoked
  when `memory.style == Style.editorial`) so the rendered memory page
  carries its own typography and works fully offline — honors ADR-001's
  "single self-contained HTML, no CDN" guarantee. Newsreader (the
  family from the original design brief) ships no Cyrillic subset on
  Google Fonts, so Source Serif 4 stands in: same `opsz` variable
  axis, same italic + roman pair, full Latin + Cyrillic coverage.
  License notes in `templates/fonts/editorial/LICENSE.md`.
- **`examples/wax_saints/` demo render.** Standalone script that
  extracts photos from a local source HTML, deduplicates by SHA-256,
  fabricates a small GPX track, and renders the editorial template
  against the example's tri-lingual text. Useful for dogfooding
  template changes without paying for a live LLM call. The `photos/`
  directory is gitignored — personal images do not land in a public
  repo.
- **Per-IP rate limit on `POST /generate`** (10 requests / hour /
  client IP, sliding window). Caps abuse cost at the
  most-expensive route — each `/generate` triggers an Anthropic
  narrative call costing ~$0.10–$0.30, so without a cap a single
  abusive IP could burn the published Anthropic spend ceiling in
  minutes. Limit lives in-process (`web.ratelimit.RateLimiter`) and
  is keyed on `Fly-Client-IP` (Fly's edge proxy) → `X-Forwarded-For`
  → socket peer; `request.client.host` alone would be useless on
  Fly because it is one of Fly's load-balancer addresses. Bounded
  to 10 000 tracked IPs (LRU-by-insert eviction) so a flood of
  unique sources cannot OOM the process. Over-quota responses are
  HTTP 429 with a `Retry-After` header, returned before the
  multipart body is parsed (FastAPI dependency runs first when the
  dep only takes `Request`). Picked over a global cap because per-IP
  bounds the worst case from a single attacker; a global counter
  would not have helped against a botnet hitting each IP once and
  would have hurt legitimate concurrent use during a launch.
- **Fly.io deployment config** (`Dockerfile`, `fly.toml`, `.dockerignore`)
  for the web builder. The image is `python:3.12-slim` with the project
  installed in editable mode so `trailstory.renderers.html` keeps
  finding the top-level `templates/` directory at runtime; the renderer
  resolves it via `Path(__file__).parents[2] / "templates"`, which only
  works when the package source lives next to `templates/` — a
  non-editable install would relocate `trailstory/` into site-packages
  and break the path. `fly.toml` ships a 512 MB shared-cpu-1x VM in
  `fra` with a `/healthz` HTTP check, `force_https`, auto-stop on idle,
  and no persistent volume — the 30-min retention sweep runs against
  `/tmp` and restarts wipe in-flight workspaces, which is *stronger*
  than the published privacy promise.
- **`/version` endpoint** that returns the running image's git SHA
  (sourced from the `GIT_SHA` build arg, falls back to `"unknown"` for
  local runs). Wired to the `make deploy` target so every Fly deploy
  stamps the current commit into the image and a deploy-correlated bug
  can be tied back to the source without log archaeology.
- **`make docker-build` / `make deploy` targets**. `deploy` refuses to
  ship a dirty working tree and forwards `GIT_SHA` to
  `flyctl deploy --build-arg`, so the SHA in `/version` always matches
  the commit Fly built from. `docker-build` exists for local smoke
  tests before the first deploy.
- **Streaming narrative generation via Server-Sent Events** for the web
  builder. `POST /generate` now runs the deterministic prep phase
  (parse GPX + load_photos + persist `pending.json`), wipes the raw
  upload via the existing `BackgroundTask`, and returns a
  `generating.html.j2` page that opens an `EventSource` to the new
  `GET /generate/{slug}/stream` endpoint. The SSE endpoint runs the
  LLM call with `AnthropicClient.complete_stream`, emits one `chunk`
  event per text delta, a `status` event when phases change
  (`writing` → `regenerating` → `rendering`), and a terminal `done`
  event with the redirect URL once the HTML has been rendered. If the
  first response does not parse as JSON, the orchestrator retries once
  (emitting a `regenerating` status); two failures land an `error`
  event without consuming further LLM calls. Pending state is unlinked
  on success so a refresh of the generating page cannot re-trigger the
  paid call (and a follow-up `GET /generate/{slug}/stream` returns 404).
  - New `AnthropicClient.complete_stream` mirrors `complete` but uses
    `messages.stream(...)` and yields each `text_stream` delta. Same
    rate-limit retry policy as `complete`, but only before the first
    chunk lands — a mid-stream error surfaces as `LLMResponseError`
    rather than retrying (re-yielding chunks the consumer already
    received would corrupt the SSE stream).
  - New `generate_narrative_stream` in `trailstory.llm.narrative`
    yields `NarrativeStreamChunk` / `NarrativeStreamRetry` /
    `NarrativeStreamComplete` events. The validated `NarrativeOutput`
    rides on the terminal event, so the SSE endpoint can build the
    `Memory`, render the HTML, and persist final `state.json` in one
    pass. Cache is intentionally bypassed for streaming runs — the
    user is watching tokens land, an instant cached return would be
    jarring; CLI / `generate_narrative` keeps the cache.
  - New `web.pipeline.prepare_pipeline` and
    `web.pipeline.stream_pipeline` split the previous `run_pipeline`
    into the prep + stream halves. `pending.json` (alongside
    `state.json` in `output/`) holds the parsed inputs the SSE
    endpoint resumes from.
- **"Save for Instagram" button on every rendered memory page**
  (`templates/styles/{editorial,log,encyclopedia}.html.j2`). The button
  POSTs to `/memory/{slug}/carousel`, fetches each slide URL as a
  `Blob`, and either calls `navigator.share({files: ...})` (iOS
  Safari path → "Save N Images" share sheet → two-tap to Instagram)
  or renders desktop fallback download links with the
  `download` attribute. The carousel slide route now sets
  `Content-Disposition: attachment; filename="<slug>-NN_role.jpg"` so
  desktop clicks save to `~/Downloads` with a meaningful, slug-
  namespaced name. The button is wired in JavaScript only — no new
  Python routes — so existing carousel infrastructure
  (`render_instagram_carousel`, `POST /memory/{slug}/carousel`) is
  reused.
- **Privacy page lifecycle polish** (`web/templates/privacy.html.j2`).
  The "what we do with your upload" section is now an explicit
  six-step lifecycle — upload → parse/resize → narrative call → HTML
  render → raw upload deletion → workspace expiry — with a
  "Verify it yourself" block that links to specific line ranges in
  `web/storage.py` (`cleanup_inputs`, `sweep_expired`) and `web/app.py`
  (`_run_sweeper`) on GitHub. The privacy link in the form
  (`web/templates/landing.html.j2`) opens in a new tab and reads
  "How we handle your photos →" so the user can audit without losing
  their upload state.

### Changed
- `POST /generate` no longer 303-redirects to the memory page. It now
  returns the new generating page (HTTP 200) with a `data-slug`
  attribute and an inline `EventSource` listener on
  `/generate/{slug}/stream`. The browser navigates to `/memory/{slug}`
  itself once the SSE `done` event arrives. Existing 4xx behaviour for
  missing / oversized / unsupported uploads is unchanged — those still
  surface synchronously from the prep phase.
- `web/dev.py::make_fake_client_factory` now stubs `complete_stream`
  alongside `complete` so `make web-dev` exercises the SSE flow
  end-to-end without paying for API calls. The stream is the same
  fixture narrative split into ~16 chunks.
- `web/storage.py` adds `Workspace.pending_state_path` for the
  intermediate JSON that `prepare_pipeline` writes and
  `stream_pipeline` consumes. The retention sweeper continues to
  garbage-collect everything older than `RETENTION_SECONDS` (30
  minutes by default).
- The unused synchronous `web.pipeline.run_pipeline` was removed —
  it was superseded by `prepare_pipeline + stream_pipeline` and only
  the streaming flow is now wired into the routes.

- **FastAPI web builder** (`web/`). Mobile-first, privacy-first, no
  accounts, no DB. Six endpoints: `GET /` (landing + builder form),
  `POST /generate` (multipart `gpx` + `photos[]` + `description` +
  `style` + optional `location` → runs the existing pipeline → 303
  redirect to `/memory/{slug}`), `GET /memory/{slug}` (serves the
  rendered HTML), `POST /memory/{slug}/carousel` (generates the
  Instagram carousel on demand and returns a JSON manifest of
  `/memory/{slug}/carousel/{filename}` slide URLs), `GET /privacy`
  (plain-language privacy page linking to the public repo), and
  `GET /healthz`. Heavy lifting is delegated to the existing
  `trailstory.gpx`, `trailstory.photos`, `trailstory.llm`, and
  `trailstory.renderers` modules — no logic is duplicated.
  - Layout: `web/app.py` is a FastAPI factory (`create_app`) that
    wires `Settings`, `Storage`, the LLM client factory, and the
    Jinja2 template environment onto `app.state`, plus a lifespan
    hook that runs the retention sweeper. `web/routes.py` holds the
    six handlers and the multipart upload validation
    (`MAX_GPX_BYTES = 50 MB`, `MAX_PHOTO_BYTES = 30 MB`,
    `MAX_PHOTOS_PER_HIKE = 60`, `.jpg/.jpeg/.heic/.heif` only) plus
    a process-local anonymous request counter.
    `web/pipeline.py` is the glue between the form and the existing
    pipeline (`Style` enum mirroring the radio buttons, `run_pipeline`
    orchestrator, `render_carousel` rebuilder). `web/storage.py`
    handles tmp-dir lifecycle (`Storage`, `Workspace`,
    `RETENTION_SECONDS = 30 min`, `SLUG_HEX_LENGTH = 12`).
  - Lifecycle / privacy: each request lands in
    `{tmp}/{slug}/` with the layout `input/{gpx,photos}` (raw
    upload, wiped via `BackgroundTask` as soon as the response is
    sent), `resized/` (privacy-stripped JPEGs from
    `trailstory.photos.load_photos`, kept for the retention window
    so the carousel route can re-render without the originals), and
    `output/{slug}.html` + `output/state.json` (the rendered page
    plus the persisted `Memory` JSON). After 30 min the in-process
    retention sweeper deletes the entire workspace.
  - Form (mobile-first): file inputs for GPX and photos with 44px
    tap targets, a 4-row textarea for the seed description, an
    optional location input, and a 3-up radio for style
    (`editorial` / `log` / `encyclopedia`, default `editorial`).
    Tailwind via the Play CDN, Alpine for tiny client-side
    reactivity, HTMX queued for the carousel POST flow — no JS build
    step. The output page itself remains the existing
    `templates/memory.html.j2`; the per-style visual variants
    described in ADR-006 land in a follow-up PR.
  - Run locally with `python -m web` (defaults to `0.0.0.0:8000`,
    `--reload` available for template iteration). Tests in
    `tests/test_web.py` (21 cases) exercise every route via
    `fastapi.testclient.TestClient` with the Anthropic client mocked
    at the `client_factory` injection point — the real SDK is never
    called. New deps in `pyproject.toml`:
    `fastapi>=0.115,<1`, `uvicorn[standard]>=0.30,<1`,
    `python-multipart>=0.0.20,<1`, plus `httpx>=0.27,<1` in `[dev]`
    for `TestClient`. `make ci` extends to lint / type-check / cover
    the new `web/` package alongside `trailstory/`.
  - **Fake-LLM dev mode.** `python -m web --fake-llm` (or
    `WEB_FAKE_LLM=1` in the environment, or `make web-dev`) swaps the
    Anthropic client factory for a deterministic stub from
    `web/dev.py` that returns the same EN/RU/DE narrative every
    time. Lets you click through the full form → pipeline → output
    flow against the bundled fixtures without paying for any API
    calls — useful for iterating on the form, the privacy page, or
    the output template. The placeholder `ANTHROPIC_API_KEY` set in
    this mode is a sentinel; it is never sent anywhere because the
    fake client short-circuits before the SDK call. Not loaded in
    the production import graph: the `web.dev` module (and its
    `unittest.mock` dependency) are imported lazily, only when the
    env flag is set.
- Dev-loop quality baseline. New `.pre-commit-config.yaml` registers
  `ruff` (with `--fix`), `ruff-format`, `detect-secrets` against the new
  `.secrets.baseline`, and the upstream `check-added-large-files` hook
  capped at 1 MB. `pre-commit>=3.7,<5` is now part of
  `[project.optional-dependencies] dev` in `pyproject.toml`, and the
  `setup` target in the `Makefile` ends with
  `pre-commit install --install-hooks` so a fresh `make setup` lands a
  developer in a state where every commit runs the same lint/format/secret
  gates. Running `make setup` again is idempotent — `pre-commit install`
  overwrites the existing hook script in place. The CI-equivalent gate
  is unchanged (`make ci`); the new hooks are an earlier, faster local
  layer on top of it.
- Python 3.13 added to the CI matrix in `.github/workflows/ci.yml`
  alongside 3.12. `fail-fast: false` is set so a regression on one
  version does not mask the other, and the `coverage-report` artifact
  upload is gated on `matrix.python-version == '3.12'` so the two
  matrix legs do not collide on the same artifact name. No dependency
  bumps were required — every entry under `[project] dependencies`
  already publishes 3.13 wheels.
- `.github/workflows/pr-title.yml` runs
  `amannn/action-semantic-pull-request@v5` on every pull request and
  fails the check if the title does not start with one of the eight
  Conventional Commits prefixes documented in `CONTRIBUTING.md`
  (`feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`,
  `style`). Subject must start with a lowercase letter to match the
  existing commit-message convention.
- `.github/dependabot.yml` opens a single weekly PR per ecosystem on
  Mondays at 06:00 Europe/Berlin: one for Python deps from
  `pyproject.toml` (minor + patch grouped under `python-minor-patch`)
  and one for GitHub Actions versions (minor + patch grouped under
  `actions-minor-patch`). Major-version bumps are still opened
  individually so each gets a dedicated `chore/` PR per
  `CONTRIBUTING.md`. Both ecosystems target `develop`, label PRs
  `type: chore`, and use `chore(deps)` / `chore(ci)` Conventional
  Commits prefixes so the new PR-title check accepts them.
- `CONTRIBUTING.md` gains a "Pre-commit hooks" subsection under "Code
  quality standards" pointing at `make setup` (which now installs
  them) and `make ci` (the CI-equivalent gate), and explaining how to
  refresh `.secrets.baseline` if `detect-secrets` flags a new
  fixture-only fake token.
- `.gitleaks.toml` extends the default gitleaks ruleset
  (`useDefault = true`) and allowlists `.secrets.baseline` so the
  Security workflow's `gitleaks` job does not flag the SHA1
  `hashed_secret` values that `detect-secrets` writes there by design.
  Without the allowlist, gitleaks's `generic-api-key` rule fires on
  any high-entropy hash inside the baseline; the hashes themselves
  reveal nothing (they only mark already-acknowledged findings owned
  by the `detect-secrets` pre-commit hook). Every other path is still
  scanned with the full default ruleset.
- Test gaps that 99% line coverage was hiding:
  - **Golden-file HTML regression test.**
    `tests/test_dev_helpers.py::test_render_with_fixtures_matches_golden`
    re-renders the bundled fixtures into a `tmp_path` and asserts byte
    equality against `tests/golden/test-render.html`. The render is
    deterministic given the same inputs (Pillow 12.2 pinned, fixed seed
    text), so any silent change to `templates/memory.html.j2`, the
    elevation-profile SVG, or the photo-encoding pipeline will trip the
    assertion. New `make golden-update` regenerates the golden after an
    intentional change.
  - **Long-title carousel bounds test.**
    `tests/test_instagram.py::test_carousel_title_stays_within_slide_bounds`
    parametrizes 1, 5, 12, and 25-word titles and asserts every drawn
    glyph stays within `SLIDE_H - 100` × `SLIDE_W - 80` via
    `ImageDraw.textbbox`. The 25-word case forced a layout fix in
    `trailstory/renderers/instagram.py`: the title font now shrinks
    dynamically from 88pt down to 40pt in 8-step decrements (`_fit_title`)
    until the wrapped block fits the title slot, instead of overflowing
    off the slide.
  - **GPX edge cases.** `tests/test_gpx.py` now covers a single-trkpt
    GPX (`parse_gpx` returns successfully; `elevation_profile` returns
    the constant `[(0.0, 0.5), (1.0, 0.5)]` fallback), a flat track
    where every elevation is identical (every y in the profile is
    exactly 0.5), and a GPX with no trkpt timestamps (`moving_time` is
    0, so `duration_min` is 0).
  - **Property-based tests.** `tests/test_properties.py` uses
    Hypothesis to drive `_slugify` (output regex `^[a-z0-9-]*$` for any
    text), `_derive_slug` (always non-empty; matches
    `^\d{4}-\d{2}-\d{2}-`), `_wrap_text` (joining the wrapped lines
    reproduces the original word sequence; no line contains a literal
    newline), and `elevation_profile` (output length always equals
    `n`; x is monotonically non-decreasing; every y is in `[0, 1]`).
    Adds `hypothesis>=6` to the dev dependency group.
  - **`MODEL` env override test.**
    `tests/test_cli.py::test_generate_passes_model_env_override_into_client`
    monkeypatches `MODEL=claude-sonnet-4-6`, captures the kwargs the CLI
    passes to `AnthropicClient`, and asserts the override flows through
    `Settings`. Locks in the documented escape hatch from CLAUDE.md.

### Changed
- Renderers now take a single `Memory` argument instead of three loose
  parameters. The `Memory` model in `trailstory/models.py`
  (`hike_input` + `gpx_stats` + `narrative` + `selected_photos`) was
  defined but unused; both
  `trailstory.renderers.html.render_html` and
  `trailstory.renderers.instagram.render_instagram_carousel` now expect
  `memory: Memory` and read `memory.narrative` /
  `memory.gpx_stats` / `memory.selected_photos` internally. The CLI
  builds one `Memory` after photo selection in `trailstory/cli.py` and
  passes the same instance to both renderers, so future renderers can
  drop in without re-plumbing the signature. Pure refactor — the
  rendered HTML is byte-identical against
  `tests/golden/test-render.html` and the existing carousel structural
  tests still pass. Tests updated with a small `_memory()` helper per
  file (`tests/test_renderers.py`, `tests/test_instagram.py`,
  `tests/conftest.py::render_with_fixtures`); the "Add a renderer"
  recipe in `CLAUDE.md` now spells out the new signature shape.
- `trailstory/renderers/instagram.py` title slide layout. The title
  font now picks the largest size between 88pt and 40pt (in 8pt steps)
  whose wrapped block fits within `TITLE_MAX_WIDTH=920` ×
  `TITLE_MAX_HEIGHT=480`, replacing the previous fixed 88pt that would
  overflow the slide on long titles. Layout constants
  (`TITLE_TOP=360`, `TITLE_LINE_SPACING=18`, font-size bracket) are
  exported as module-level `Final[int]` so the new bounds test in
  `tests/test_instagram.py` can re-run the exact same layout
  computation when measuring drawn glyph extents.

### Security
- Pillow upgraded from `>=10.3,<12` to `>=12.2,<13` in `pyproject.toml`,
  which picks up the fixes for CVE-2026-25990 (Pillow 12.1.1) and
  CVE-2026-40192 (Pillow 12.2.0). Both advisories were surfaced by the
  pip-audit job introduced alongside the security baseline and were
  temporarily allow-listed in `.pip-audit-allowlist.txt`; the allowlist
  is now empty again. `make ci` (203 tests, including the EXIF
  GPS-strip and pixel-bomb cases) and `make test-render` both pass on
  Pillow 12.2.0 — the private `Exif._ifds` cache used by the GPS strip
  in `trailstory/photos.py` still behaves the same way under the new
  version.

### Fixed
- `.github/workflows/security.yml` no longer combines `--strict` with
  `--skip-editable`, which conflict: `--strict` re-escalates the
  editable-skip warning into a fatal error, so the dep-audit job was
  failing on every run with `ERROR: trailstory: distribution marked as
  editable` regardless of whether real CVEs were present. Dropping
  `--strict` keeps the job's actual signal (non-zero exit on a real
  CVE) intact while letting the local editable install be skipped
  cleanly.

### Added
- Defense-in-depth security baseline. `.github/workflows/security.yml`
  runs on every push to `develop` and every PR with two jobs:
  `gitleaks/gitleaks-action@v2` for secret scanning across the full
  branch history, and `pip-audit --strict` against the installed
  dependency tree. Vulnerabilities can be allow-listed (one CVE / GHSA
  / PYSEC id per line, with a comment) in `.pip-audit-allowlist.txt`,
  which is read by the workflow and starts empty. `SECURITY.md` at the
  repo root captures the threat model (single-user CLI, output shared
  via messengers), what NOT to do (no committing `.env`, no pasting
  keys into issues or logs), and the rotate-then-rebase steps if an
  Anthropic key leaks; it cross-references
  [ADR-001](docs/adr/001-base64-photo-embedding.md) and the EXIF GPS
  strip in `trailstory/photos.py`.
- `Image.MAX_IMAGE_PIXELS = 200_000_000` set at the top of
  `trailstory/photos.py` rejects decompression bombs (Pillow raises
  `Image.DecompressionBombError`, which `load_photos` now wraps in
  `PhotoLoadError`) while staying generous for any legitimate phone or
  full-frame camera. `tests/test_photos.py` covers the wrap by
  monkeypatching the threshold lower against a small fixture.
- Length cap on `HikeInput.seed_text` (`Field(max_length=1000)` in
  `trailstory/models.py`) — Pydantic raises `ValidationError` on
  overflow and the existing CLI error handling surfaces it cleanly.
  New `tests/test_models.py` covers the boundary.
- Prompt-injection guard appended to `SYSTEM_NARRATIVE` in
  `trailstory/llm/prompts.py`: the model is instructed to treat the
  seed text as untrusted prose, never change languages or output
  format based on its content, and never reveal or modify the system
  instructions. `tests/test_prompts.py` asserts the clause is present.
- Five new `Settings` fields covering output preferences that were
  previously hardcoded: `photo_max_edge` (`PHOTO_MAX_EDGE`, default
  `1800`), `photo_quality` (`PHOTO_QUALITY`, default `90`),
  `instagram_quality` (`INSTAGRAM_QUALITY`, default `90`),
  `narrative_max_tokens` (`NARRATIVE_MAX_TOKENS`, default `4096`), and
  `narrative_max_retries` (`NARRATIVE_MAX_RETRIES`, default `3`). The
  CLI plumbs each value into `load_photos`,
  `render_instagram_carousel`, and `AnthropicClient` so a hike can be
  re-rendered with smaller embedded JPEGs or a longer retry budget
  without code changes. Module-level `DEFAULT_*` constants in
  `trailstory/photos.py`, `trailstory/renderers/instagram.py`, and
  `trailstory/llm/client.py` continue to back direct construction
  outside the CLI. `.env.example` documents each new variable, and
  `tests/test_config.py` covers env-var → field round-trips plus an
  end-to-end check that `PHOTO_MAX_EDGE=400` produces 400px-edge JPEGs.
- Project-local Claude Code workflow under `.claude/`: a tracked
  `settings.json` that pre-allows the read-only and project-specific Bash
  commands the toolchain actually needs (`make ci`, `make eval`,
  `make eval-live`, `make test-render`, `make generate`, `.venv/bin/{pytest,ruff,mypy,python,trailstory}:*`,
  read-only `git`/`gh` queries) so the agent stops prompting for them every
  run; plus a `PostToolUse` hook on `Edit|Write` that prints a reminder
  whenever `llm/prompts.py` or `llm/narrative.py` is touched, telling the
  developer to run `make eval` (and `make eval-live` for prompt changes)
  before merging. Personal overrides still belong in
  `.claude/settings.local.json` (gitignored).
- Project slash commands under `.claude/commands/`:
  - `/eval` — runs `make eval`, parses the per-case rubric output,
    summarizes pass/fail in a Markdown table, suggests likely fixes per
    failing check (without auto-editing prompts or goldens).
  - `/eval-live` — runs `make eval-live`, prints rubric + judge tables
    with per-axis golden deltas, and explicitly confirms with the user
    before treating any new score as the new golden.
  - `/render-test` — runs `make test-render`, prints the absolute output
    path, suggests `open <path>` on macOS / `xdg-open` on Linux.
  - `/ship` — sanity-checks the working tree and branch name, runs
    `make ci`, drafts a Conventional Commits message from the diff,
    pauses for confirmation before committing, pushing, and opening a
    PR against `develop` using the project PR template.
  - `/sync-develop` — fetches origin, fast-forwards local `develop`,
    lists local branches whose tip is reachable from `origin/develop`
    or whose remote is `gone`, and asks the user before deleting any.
- Three structured GitHub issue templates under `.github/ISSUE_TEMPLATE/`
  (form-based YAML, labelled and pre-titled):
  - `narrative-quality.yml` — captures the seed text, GPX summary,
    baby/age, the specific dimension that reads wrong, expected-vs-actual
    snippets, writer model, cache state, and a triage checkbox to add the
    failure as a new case under `tests/eval/cases/`.
  - `bug.yml` — repro steps, expected vs actual, OS, Python version, and
    the output of `pip show trailstory anthropic pydantic pillow`.
  - `feature.yml` — what / why / non-goals / suggested PR shape.
  Replaces the older `bug_report.yml` and `feature_request.yml`.
- Initial project structure, CI pipeline, and developer tooling.
- HTML renderer producing a self-contained, bilingual memory page with
  photos embedded as base64 data URIs and an inline elevation-profile SVG
  (`trailstory.renderers.html.render_html`, `templates/memory.html.j2`).
- `trailstory generate` CLI: orchestrates the full pipeline (GPX → photos
  → narrative → HTML) with Rich progress output, `--photos`, `--gpx`,
  `--seed`, `--name`, `--age`, `--out`, and `--location` options.
- Instagram carousel renderer: `trailstory generate --instagram` writes
  1080×1350 portrait JPEG slides under `output/{slug}/carousel/` (title +
  N photos center-cropped to 4:5 + closing pull-quote).
- Content-addressed narrative cache (`trailstory.llm.cache`): the LLM
  call is keyed on the SHA-256 of the GPX bytes, the per-photo bytes,
  the parent's seed/baby/location inputs, and the model identifier, then
  stored under `~/.cache/trailstory/narratives/<key>.json`. Iterating on
  the HTML or Instagram renderer no longer re-spends an Opus call per
  run. New `--no-cache` flag on `trailstory generate` forces a fresh
  call. `NarrativeOutput.schema_version` (defaults to `1`) lets the
  cache auto-invalidate stale shapes after a model evolution.
- Programmatic narrative-quality regression suite (`tests/eval/`): a
  pure-Python rubric (`tests/eval/rubric.py`) that checks schema
  round-trip, paragraph counts, Cyrillic coverage with an ASCII-run
  guard against mid-paragraph English fallbacks, EN/RU word-count
  ratio, title/subtitle/milestone length caps, photo-index validity,
  and pull-quote overlap with the body. Three fixture cases
  (baseline, joyful, exhausted/foggy) drive a CLI runner
  (`python -m tests.eval.run --all`, also wired up as `make eval`)
  that calls the real Anthropic API with the cache disabled, applies
  the rubric, prints a per-case table, and exits non-zero on any
  failure. Unit tests (`tests/test_eval_rubric.py`) exercise every
  rubric function on hand-built fixtures and run in `make ci` for
  free. Design recorded in
  [`docs/adr/003-narrative-eval-suite.md`](docs/adr/003-narrative-eval-suite.md);
  the CLAUDE.md "Update a prompt" recipe now requires running the
  eval before merging a prompt change.
- Paid LLM-as-judge layer on top of the rubric
  (`tests/eval/judge.py`, `tests/eval/judge_prompts.py`). Scores
  every generated narrative on four taste-level axes — `warmth`,
  `narrative_arc`, `russian_fidelity`, `photo_selection_plausibility`
  — plus free-form `notes`, validated through a `JudgeScore` Pydantic
  model. Judge defaults to `claude-sonnet-4-6` (different family from
  the writer to reduce same-model score inflation) and is
  configurable via the `EVAL_JUDGE_MODEL` env var. New runner flag
  `python -m tests.eval.run --all --live-judge` and Makefile target
  `make eval-live` chain rubric + judge; `make eval-update-golden`
  rewrites both narrative and judge goldens in one paid run. When a
  `tests/eval/golden/<case>-judge.json` exists, the runner prints
  per-axis deltas and exits non-zero if any axis dropped by
  `EVAL_REGRESSION_THRESHOLD` (default `1.0`) vs golden. Drift-guard
  tests (`tests/test_eval_judge_prompts.py`) keep the prompt's JSON
  skeleton in sync with `JudgeScore`; unit tests
  (`tests/test_eval_judge.py`) exercise the happy path, both retry
  paths, and bounds-validation against a mocked client and run in
  `make ci` for free. Rationale and tradeoffs added to
  [`docs/adr/003-narrative-eval-suite.md`](docs/adr/003-narrative-eval-suite.md);
  the CLAUDE.md "Update a prompt" recipe now reads
  "`make eval` (free) → `make eval-live` (paid) → post both score
  tables in the PR".

### Changed
- `CLAUDE.md` restructured to make agent-assisted work faster: new
  top-level **Glossary** (slug, milestone, hero, narrative, subtitle, pull
  quote, carousel, memory, eval, golden, judge), **Common tasks** recipe
  block (add a field to `NarrativeOutput`, tune a prompt, add a renderer,
  add an eval case, change the output page design — replaces and tightens
  the older "How to make common changes" section while preserving the
  Jinja2 context reference), and **Decision register** linking ADR-001
  (base64 photos), ADR-002 (Opus writer model), and ADR-003 (eval suite),
  with a one-liner reminder to read the relevant ADR before changing
  anything in its area. The Getting-help section now points at the new
  issue templates.
- `tests/fixtures/sample_photos/` now contains twelve images instead of
  five. The narrative prompt asks the model to pick 6-8 photo indices,
  so the previous five-photo fixture made `make eval`'s `indices_valid`
  rubric check unsatisfiable by construction. The seven new entries
  (`06_meadow`, `07_creek`, `08_lunch`, `09_baby_carrier`, `10_clouds`,
  `11_descent`, `12_cabin`) interleave with the original five in EXIF
  time, so the chronological-sort path is still exercised. Regenerate
  with `python scripts/generate_sample_photos.py`.

### Fixed
- **Privacy:** `trailstory.photos.load_photos` now strips the GPS sub-IFD
  (EXIF tag `0x8825`) from every resized JPEG and bakes EXIF orientation
  into pixels via `PIL.ImageOps.exif_transpose`. Previously the resized
  photos preserved EXIF wholesale, and the HTML renderer base64-embeds
  those JPEGs verbatim — meaning every shareable `.html` produced before
  this fix carries the precise GPS coordinates of every selected photo.
  Camera make/model, lens, and timestamp tags are kept; only GPS is
  removed. **Previously-rendered `.html` files still contain the leaked
  GPS data** — if location privacy matters, re-render those memories from
  source and re-share the new files; the originals already in recipients'
  inboxes cannot be recalled.

---

<!-- Template for new releases:

## [X.Y.Z] - YYYY-MM-DD

### Added
- New features.

### Changed
- Changes to existing behaviour.

### Deprecated
- Soon-to-be removed features.

### Removed
- Features removed this release.

### Fixed
- Bug fixes.

### Security
- Security fixes.

-->
