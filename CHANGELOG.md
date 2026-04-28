# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
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
