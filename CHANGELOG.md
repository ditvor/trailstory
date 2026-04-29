# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
