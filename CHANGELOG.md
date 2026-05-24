# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
