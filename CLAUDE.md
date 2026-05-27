# Trailstory — Claude Code context

This file tells you everything you need to know to contribute to this codebase.
Read it before writing any code. It answers: what does this do, how is it structured,
and what are the decisions already made that you must not reverse.

---

## What this project does

Trailstory is a command-line tool that takes hiking inputs (GPX track, photos, a short
emotional description from the hiker) and produces a beautiful, shareable memory page.

The core user: a hiker living in Munich who wants to share those experiences with
family abroad — including in Russia, where Instagram and some messaging platforms
are blocked — and with German-speaking neighbours and in-laws nearby.

The output is a **single self-contained HTML file** that:
- Works in any browser without internet access or CDN
- Can be sent as a file via WhatsApp, email, or any messenger
- Has a tri-lingual toggle (English / Russian / German)
- Contains an embedded elevation profile SVG
- Has share buttons (WhatsApp, copy link, Instagram export prompt)

This is **not** a fitness tracker. It is a memory tool.
Stats (distance, elevation) appear in the output but serve the narrative, not the other way around.

---

## Glossary

One-liners. When in doubt, this is the meaning the codebase intends.

- **slug** — url-safe hike identifier (e.g. `2025-05-tegernsee-fog`). Available
  inside the template as `{{ meta.slug }}` and used as the output directory
  and HTML filename.
- **narrative** — the LLM-generated content (`NarrativeOutput`): tri-lingual
  title, subtitle, exactly six chapters (each with its own body, bound
  photo, and per-chapter title / time / place metadata), pull quote, and
  milestone. Each user-facing field is a `LocalizedString` (or, for
  paragraph blocks, a sentence-leveled `Paragraph`) carrying `en` /
  `ru` / `de` variants. See ADR-015.
- **chapter** — one stop in the hike (`Chapter`): `id` + `time` +
  `place` + `lat`/`lon` + tri-lingual `title` + sentence-leveled `body`
  + one bound `photo_index`. Six per narrative. The Trailpath layouts
  (Letter / Zine / Sunday / Postcard / Album) all consume this shape.
- **LocalizedString** — small Pydantic model with `en` / `ru` / `de` string
  fields. The shape that lets one prompt + one LLM call produce all three
  languages at once. See ADR-005.
- **subtitle** — the short `narrative.subtitle.en` / `.ru` / `.de` line under
  the title. Sets the emotional tone in one sentence.
- **pull quote** — the `narrative.pull_quote.en` / `.ru` / `.de` callout
  rendered large in the page body. Pulled from the seed text or close to it.
- **milestone** — the `narrative.milestone.en` / `.ru` / `.de` badge ("First
  mountain hike", "First time above the fog").
- **hero** — the top header block of the rendered memory page: title +
  subtitle + meta line (date, location, distance, elevation, duration). See
  `header.hero` in `templates/memory.html.j2`.
- **memory** — the `Memory` model: `hike_input` + `gpx_stats` + `narrative`
  + `selected_photos` resolved from indices. Every renderer takes a `Memory`
  and returns a `Path`.
- **carousel** — the Instagram output: 1080×1350 portrait JPEGs under
  `output/{slug}/carousel/`. Produced by `trailstory.renderers.instagram` when
  `--instagram` is passed.
- **eval** — the narrative-quality regression suite under `tests/eval/`.
  Programmatic rubric (always-on, paid only for the writer call) plus an
  optional LLM-as-judge layer (paid, `--live-judge` / `make eval-live`). See
  ADR-003.
- **golden** — saved baseline output under `tests/eval/golden/`. Each case
  has `<case>.json` (the writer's `NarrativeOutput`) and `<case>-judge.json`
  (the judge's per-axis scores). Refresh deliberately with
  `make eval-update-golden`.
- **judge** — the LLM-as-judge layer (`tests/eval/judge.py`). Defaults to
  `claude-sonnet-4-6` (different family from the writer to reduce same-model
  inflation). Configurable via `EVAL_JUDGE_MODEL`.

---

## Tech stack

| Layer | Library | Why |
|-------|---------|-----|
| CLI | `click` | Clean, composable, testable |
| Config | `pydantic-settings` | Type-safe env vars, fails loudly if key is missing |
| Data models | `pydantic` v2 | Strict validation, the contract between all layers |
| GPX parsing | `gpxpy` | Battle-tested, clean API |
| Image processing | `Pillow` | EXIF, resize, Instagram carousel generation |
| LLM | `anthropic` Python SDK | Direct access, streaming support for future use |
| Templating | `jinja2` | The HTML template is complex; belongs in a file, not a string |
| Testing | `pytest` | Standard, good fixture system |
| Lint + format | `ruff` | Fast, replaces black + isort + flake8 |
| Type checking | `mypy` | Catches model field mismatches early |
| Output | Rich `console` | Never use `print()` |
| Web service | `fastapi` + `uvicorn` | Same pipeline behind a mobile-first form (`web/`) |
| Form parsing | `python-multipart` | Required by FastAPI for `UploadFile` / `Form` |

---

## Architecture and file map

```
trailstory/
├── cli.py              Entry point. Click commands. Thin — no business logic here.
├── config.py           pydantic-settings. Reads .env. Single Settings instance.
├── models.py           ALL Pydantic models. This is the source of truth for data shapes.
├── gpx.py              GPX parsing → GpxStats. Also produces elevation_profile() points.
├── photos.py           Load from dir, sort by EXIF datetime, resize. No LLM logic here.
├── llm/
│   ├── __init__.py
│   ├── client.py       Anthropic API wrapper. Retry logic. Never called outside llm/.
│   ├── prompts.py      ALL prompt strings. Constants only. No logic.
│   └── narrative.py    Orchestrates LLM calls. Validates response against NarrativeOutput.
└── renderers/
    ├── __init__.py
    ├── html.py         Jinja2 → .html. Embeds photos as base64 data URIs.
    └── instagram.py    Pillow → carousel images for Instagram.

templates/
└── memory.html.j2      The shareable memory page. Edit this to change the design.

web/                    FastAPI builder. Wraps the existing pipeline behind a form.
├── __init__.py         Re-exports create_app.
├── __main__.py         python -m web → uvicorn entry point. --fake-llm for offline UI work.
├── app.py              FastAPI factory + lifespan-driven retention sweeper.
├── dev.py              Fake LLM client factory used when WEB_FAKE_LLM=1 (UI iteration only).
├── routes.py           Six handlers: /, /generate, /memory/{slug}, /memory/{slug}/carousel, /privacy, /healthz.
├── pipeline.py         Style enum + glue between an upload and the trailstory pipeline.
├── storage.py          Workspace = {root}/{slug}/{input,resized,output}/. 30-min retention.
├── templates/          Jinja2 templates for the builder UI (separate from the output page).
└── static/             Tailwind via CDN; no JS build step.

tests/
├── conftest.py         Shared fixtures. Fake API key injection. Sample data.
├── fixtures/
│   ├── sample.gpx      A real-shaped GPX file for testing.
│   └── sample_photos/  5 small JPEG files with EXIF timestamps.
├── test_gpx.py
├── test_photos.py
├── test_narrative.py   Always mocks the Anthropic client. Never calls the real API.
├── test_renderers.py
└── test_web.py         Routes via fastapi.testclient.TestClient with the LLM mocked.

docs/adr/               Architecture Decision Records — why decisions were made.
```

---

## Data flow

```
HikeInput (user CLI args)
    │
    ├─► gpx.py          → GpxStats
    ├─► photos.py       → list[PhotoMeta]
    │
    ▼
llm/narrative.py        takes HikeInput + GpxStats + list[PhotoMeta]
    │                   calls Anthropic with structured prompt
    │                   validates JSON response
    ▼
NarrativeOutput         (tri-lingual title, subtitle, six chapters, pull quote, milestone)
    │                   each chapter binds one photo via chapter.photo_index
    │
    ├─► renderers/html.py       → {slug}.html
    └─► renderers/instagram.py  → carousel/*.jpg  (only if --instagram flag)
```

---

## Models — `models.py` is the contract

All data shapes live here. When you add a new field to the LLM output, add it to the
Pydantic model first. The rest of the code follows.

Key models:

```python
class GpxStats:
    distance_km: float
    elevation_gain_m: float
    duration_min: int
    start_elevation_m: float
    summit_elevation_m: float
    waypoints: list[Waypoint]           # (lat, lon, ele, time) tuples
    elevation_profile: list[tuple[float, float]]  # 20 normalised (x, y) points for SVG

class PhotoMeta:
    path: Path
    timestamp: datetime
    index: int                          # 0-based, used by LLM for selection

class HikeInput:
    gpx_path: Path
    photos_dir: Path
    seed_text: str                      # the hiker's 2-3 sentence emotional description
    location_name: str | None           # auto-detected from GPX if not provided

class LocalizedString:
    en: str
    ru: str
    de: str

class Sentence:                         # ADR-014
    text: LocalizedString
    provenance: Provenance              # source: SEED | PHOTO | GPX | INFERRED

Paragraph = list[Sentence]

class Chapter:                          # ADR-015 — one stop in the hike
    id: str                             # short slug, "arrival" / "river" / ...
    time: str                           # "HH:MM"
    place: LocalizedString              # locality
    lat: float
    lon: float
    title: LocalizedString              # short noun phrase
    body: Paragraph                     # 2-4 sentences with provenance
    photo_index: int                    # one bound photo per chapter

class NarrativeOutput:
    schema_version: int = 4
    title: LocalizedString
    subtitle: LocalizedString
    chapters: list[Chapter]             # exactly 6 (CHAPTER_COUNT)
    pull_quote: LocalizedString
    milestone: LocalizedString          # e.g. "First mountain hike"
    # selected_photo_indices is a read-only @property over chapters[*].photo_index;
    # paragraphs_as_localized() is a computed view over chapters[*].body for
    # legacy consumers (carousel, eval rubric flat-text checks).

class Memory:
    hike_input: HikeInput
    gpx_stats: GpxStats
    narrative: NarrativeOutput
    selected_photos: list[PhotoMeta]    # ordered to match chapters (one per chapter)
```

---

## LLM interaction rules

### All prompts live in `llm/prompts.py`

Never write a prompt string anywhere else. If you are adding a new LLM call and you
find yourself writing a prompt in `narrative.py` or any other file — stop, move it to
`prompts.py` first.

Prompts are module-level constants:

```python
SYSTEM_NARRATIVE: str = """..."""
USER_NARRATIVE_TEMPLATE: str = """..."""   # uses .format() or str.Template
```

### Model to use

Default is `claude-opus-4-7` — quality is the priority for the narrative, since
it is the user-facing creative output and the per-hike cost difference vs sonnet
is negligible. Decision and trade-offs recorded in
[`docs/adr/002-narrative-model-choice.md`](docs/adr/002-narrative-model-choice.md).
The model name is set in `config.py` and can be overridden per-run via the
`MODEL` env var. Switching to a sonnet- or haiku-class model is allowed when
quality is satisfactory and you want to reduce cost — but only by overriding
the env var, not by changing the default without a new ADR.

### Response validation

The LLM must return valid JSON matching `NarrativeOutput`. The validation flow:

1. Call LLM with prompt that includes the JSON schema
2. Parse response as JSON → if it fails, retry once with a correction prompt
3. Validate parsed dict against `NarrativeOutput` via Pydantic → if it fails, raise `NarrativeGenerationError`
4. Never pass unvalidated LLM output downstream

### Error handling

```python
class NarrativeGenerationError(Exception): ...
class LLMRetryExhaustedError(Exception): ...
```

These are the only exception types that should escape `llm/`. Catch `anthropic.APIError`
inside `client.py` and convert it to one of these.

---

## Key design decisions

### 1. Photos are embedded as base64 data URIs

The HTML output embeds every selected photo as a base64 data URI. The file is larger
but fully self-contained — no server, no CDN, no internet required to view it.

This is intentional: the primary recipients are family members in Russia who may receive
the file via Telegram, WhatsApp, or email, and need it to work in any browser.

Do not change this to use relative image paths without opening an ADR.

See `docs/adr/001-base64-photo-embedding.md`.

### 2. The LLM selects photos, not the user

The user provides a directory of photos. The LLM receives a numbered list and binds
one photo to each of six chapters that together tell the best narrative arc
(opening, effort, landscape, a human-detail beat drawn from the seed, summit-or-
endpoint). The user does not curate.

This is a core UX decision. Do not add a `--select-photos` flag without discussion.
See ADR-015 for the chapter-binding contract.

### 3. Tri-lingual output is the default, not an option

Every user-facing `NarrativeOutput` field is a `LocalizedString` (or, for paragraph
blocks, a sentence-leveled `Paragraph`) carrying `en` / `ru` / `de` variants —
produced in a single LLM call. The HTML template renders all three; the reader
cycles through with a button. There is no `--language` flag.

To add a fourth language, add the field to `LocalizedString`, update the prompt's
JSON skeleton, extend the template's body class swap, and refresh goldens — all in
one PR. Do not add partial language support. See ADR-005.

### 4. The HTML template is a Jinja2 file, not a string in Python

`templates/memory.html.j2` is the source of the output page. HTML and CSS belong there.
No HTML strings in Python files.

---

## Config and secrets

```python
# config.py
class Settings(BaseSettings):
    anthropic_api_key: SecretStr    # read from ANTHROPIC_API_KEY env var
    model: str = "claude-opus-4-7"
    output_dir: Path = Path("./output")
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
```

**Rules:**
- The API key is typed as `SecretStr`. It will never appear in logs or `repr()` output.
- Call `settings.anthropic_api_key.get_secret_value()` only inside `llm/client.py`.
- If the key is missing, `pydantic-settings` raises at startup with a clear message.
- Tests inject `ANTHROPIC_API_KEY=sk-test-fake` via a `conftest.py` fixture.

---

## Testing rules

- **Never call the real Anthropic API in tests.** Always mock `llm/client.py`.
- **Use `tests/fixtures/`** for sample GPX and photo files. Keep them small.
- **Test the contract, not the implementation.** For the renderer, check that the output
  HTML contains the expected title text — not that `jinja2.Environment()` was called once.
- **One test file per module.** `test_gpx.py` tests `gpx.py`, etc.
- **Coverage ≥ 80%** on `trailstory/` (excluding `cli.py`).

---

## Common tasks

Quick reference: intent → recipe. Slash-command shortcuts live in
`.claude/commands/` (`/eval`, `/eval-live`, `/render-test`, `/ship`,
`/sync-develop`).

### Add a field to `NarrativeOutput`

1. Add the field to `NarrativeOutput` in `trailstory/models.py`. User-facing
   strings should be `LocalizedString` (en / ru / de). Per-chapter fields
   go on `Chapter`. Do not invent flat per-language fields (ADR-005).
2. Update the JSON skeleton in `llm/prompts.py` so the model is instructed to
   produce it (with the `en` / `ru` / `de` keys when the field is localized).
   Leave the previous version as a dated comment.
3. Update mocks: `tests/test_narrative.py`, `tests/test_cli.py`,
   `tests/test_renderers.py`, `tests/test_instagram.py`,
   `tests/test_cache.py`, `tests/test_web.py`, `tests/conftest.py`. The
   `chapters_from_strings` / `chapters_dict_from_strings` helpers and
   any stub `NarrativeOutput` constructor must include the new field —
   otherwise `make ci` and `/render-test` will break.
4. Reference the field in the editorial style template
   (`templates/styles/editorial.html.j2`) — one `<span class="en">` /
   `ru` / `de` block per language, or `narrative.<field>.<lang>`
   directly.
5. If it appears in the carousel, update `trailstory/renderers/instagram.py`
   (the carousel reads the English variants only).
6. `make ci` (free) → `make eval` (paid writer call). Confirm the model
   populates the new field cleanly across every case before opening the PR.

### Tune a prompt

1. Edit the constant in `llm/prompts.py`. Leave the previous version as a
   dated comment.
2. `make eval` — programmatic rubric, paid writer call per case.
3. `make eval-live` — adds the paid judge call per case; scores against
   `tests/eval/golden/<case>-judge.json` with regression threshold
   `EVAL_REGRESSION_THRESHOLD` (default `1.0`).
4. Paste **both** score tables (rubric and judge) in the PR description. Do
   not merge if either regressed without explicit reasoning.
5. If the new output is the intended new baseline, refresh both narrative
   and judge goldens with `make eval-update-golden` and explain the refresh
   in the PR. Background and trade-offs in
   [`docs/adr/003-narrative-eval-suite.md`](docs/adr/003-narrative-eval-suite.md).
6. If the output schema changes, update `NarrativeOutput` first (see above).
7. Label the PR `llm`.

### Add a renderer

1. Create `trailstory/renderers/<name>.py` with
   `def render_<name>(*, memory: Memory, output_dir: Path, slug: str, …) -> Path | list[Path]`.
   Read inputs from `memory.narrative`, `memory.gpx_stats`, and
   `memory.selected_photos` — never recompute or re-derive them. Existing
   renderers (`render_html`, `render_instagram_carousel`) are the
   reference shape.
2. Wire it into `trailstory/cli.py` behind a new `--<name>` flag — off by
   default, opt-in. The CLI builds one `Memory` after photo selection and
   passes the same instance to every renderer; reuse it.
3. Add `tests/test_<name>.py` with a small `_memory()` helper per file
   (see `tests/test_renderers.py` and `tests/test_instagram.py` for the
   pattern). Never call the real Anthropic API in unit tests.
4. Add an entry under `### Added` in `CHANGELOG.md`.
5. `make ci` must pass. For visual changes, run `/render-test` and eyeball
   the output before opening the PR.

### Add an eval case

1. Create `tests/eval/cases/<NN>-<slug>.json`. Required fields (see existing
   cases for the exact shape): `name`, `gpx_path`, `photos_dir`, `seed_text`,
   `location_name`.
2. Run
   `python -m tests.eval.run --case <NN>-<slug> --live-judge --update-golden`
   to produce both `tests/eval/golden/<NN>-<slug>.json` and
   `tests/eval/golden/<NN>-<slug>-judge.json`. Paid: one writer call + one
   judge call.
3. Read both golden files. If anything looks wrong, tune the prompt or seed
   text and regenerate **before committing** — once the goldens are in, the
   regression gate compares against them.
4. Commit the case file and both goldens together.

### Change the output page design

Edit `templates/styles/<style>.html.j2` (v0 ships `editorial.html.j2`
only). The top-level `templates/memory.html.j2` is a thin shell that
`{% include %}`s the right style template. Available context variables:

```
{{ narrative }}     NarrativeOutput — narrative.chapters is the six-chapter list;
                    narrative.title.en/.ru/.de etc. for tri-lingual scalars.
                    narrative.paragraphs_as_localized() flattens chapter bodies
                    per language for legacy flat-text consumers.
{{ stats }}         GpxStats object
{{ photos }}        list of dicts { 'data_uri': str, 'index': int }, in chapter order
{{ elevation }}     list of (x, y) tuples for the elevation sparkline
{{ meta.date }}     formatted hike date
{{ meta.location }} display location label
{{ meta.slug }}     url-safe hike identifier
{{ meta.style }}    style value (editorial)
{{ fonts }}         dict of base64 WOFF2 payloads (editorial only; empty for other styles)
```

After editing, run `make test-render` (or `/render-test`) to produce a test
HTML in `output/test/` and open it in a browser before opening the PR.

### Iterate on the web builder UI

1. `make web-dev` — runs `python -m web --fake-llm --reload` so the form,
   the privacy page, and the output template can be exercised end-to-end
   against `tests/fixtures/sample.gpx` + `tests/fixtures/sample_photos/`
   without paying for any API calls. The fake client returns the same
   EN/RU/DE narrative every time; useful for layout iteration, useless
   for narrative quality work (use `make eval` for that).
2. `make web` — runs the same server but with the real Anthropic client
   (`ANTHROPIC_API_KEY` required). Use this when you need a fresh
   narrative for screenshot or QA purposes.
3. To check mobile rendering, open Chrome DevTools and switch to
   responsive mode at 375px width. The form uses 44px tap targets and a
   single-column layout; anything that breaks on a 375px viewport is a
   regression.

---

## Anti-patterns — do not do these

| What | Why |
|------|-----|
| `print()` for output | Use `rich.console`. `print()` breaks CI output formatting. |
| Prompt strings outside `llm/prompts.py` | Makes prompts impossible to audit or version. |
| HTML/CSS strings in Python | Belongs in `templates/memory.html.j2`. |
| `settings.anthropic_api_key` outside `llm/client.py` | API key access must be centralised. |
| `from trailstory.config import settings` at module level | Breaks tests that need to inject a fake key. Inject via argument. |
| Bare `except:` | Always catch a specific exception. |
| `# type: ignore` without a comment | Explain why mypy is wrong, or fix the type. |
| Mutable default arguments | `def f(items=[])` is a bug. Use `None` and assign inside. |
| Committing `.env` | It is in `.gitignore`. If you accidentally add it, rotate the key immediately. |

---

## Decision register

Architecture Decision Records live under [`docs/adr/`](docs/adr/). The *why*
behind each load-bearing decision is recorded there so future contributors
don't relitigate them.

1. [ADR-001 — embed photos as base64 data URIs](docs/adr/001-base64-photo-embedding.md):
   the HTML output must work offline and over messengers; relative image
   paths break that. Don't switch to relative paths without a new ADR.
2. [ADR-002 — narrative writer model is `claude-opus-4-7`](docs/adr/002-narrative-model-choice.md):
   quality wins over per-call cost for the user-facing creative output.
   Override per-run via the `MODEL` env var; never change the default
   without a new ADR.
3. [ADR-003 — narrative-quality eval is a programmatic rubric, with a paid
   LLM-judge layer added separately](docs/adr/003-narrative-eval-suite.md):
   one always-on rubric for structural failures (unit tests run free in
   `make ci`; `make eval` adds the paid writer calls) plus an opt-in paid
   judge layer for taste-level axes (`make eval-live`). Goldens under
   `tests/eval/golden/` are the regression baseline; refresh deliberately
   and explain in the PR.
4. [ADR-004 — `HikeInput` carries no baby fields](docs/adr/004-remove-baby-fields-from-hike-input.md):
   `baby_name` and `baby_age_months` are gone. The seed text is the only
   subject context the prompt sees. Privacy and audience-breadth wins;
   `seed_text` becomes load-bearing for family flavour. Don't add the
   fields back without a new ADR.
5. [ADR-005 — `LocalizedString { en, ru, de }`](docs/adr/005-localized-string-and-german-output.md):
   every user-facing narrative field is one nested `LocalizedString` instead
   of two flat `_en` / `_ru` strings. EN, RU, and DE are produced in a
   single LLM call. Adding a fourth language is one Pydantic field +
   prompt-skeleton edit + template arm + golden refresh.
6. [ADR-006 — one narrative, many visual treatments](docs/adr/006-three-visual-styles-share-one-narrative.md):
   the rendering treatments of one prompt's output, not separate prompt
   families. The eval suite stays calibrated against the editorial
   register. ADR-006 originally named `editorial` / `log` /
   `encyclopedia`; ADR-015 trimmed `log` and `encyclopedia` and the v0
   product decision is to ship the five Trailpath styles (Letter, Zine,
   Sunday, Postcard, Album) instead — Letter is `editorial` in this
   PR; the other four land in subsequent renderer PRs.
7. [ADR-007 — faithfulness eval axis](docs/adr/007-faithfulness-eval-axis.md):
   the paid LLM judge now extracts every concrete claim from the
   narrative and labels it `SUPPORTED` / `INFERRED` / `UNSUPPORTED`. A
   derived `faithfulness` score (`@computed_field` on `JudgeScore`,
   0-5) is gated like every other judge axis. Phase 0 of the
   narrative-faithfulness initiative; downstream phases (prompt
   anti-fabrication, two-pass fact ledger, multimodal grounding,
   sentence-level provenance) will record their own ADRs.
8. [ADR-008 — writer prompt temporal grounding + anti-fabrication](docs/adr/008-writer-prompt-temporal-grounding-and-anti-fabrication.md):
   Phase 1 of the narrative-faithfulness initiative. The writer prompt
   now receives the GPX-derived hike date and an inferred season
   ("spring (April; northern hemisphere)"); a new clause forbids
   ungrounded concrete specifics (animals, foods, named objects) while
   permitting generic nature words. Closes the prompt-engineering
   ceiling; Phase 2 (two-pass writer with `FactLedger`) is the
   structural fix.
9. [ADR-009 — two-pass narrative pipeline with `FactLedger`](docs/adr/009-two-pass-narrative-with-fact-ledger.md):
   Phase 2. A cheap Haiku-class extractor reads the seed + GPX + photo
   timestamps and emits a typed `FactLedger` (people, weather,
   chronology beats with `objects_mentioned`); the Opus writer consumes
   the serialized ledger as its sole input — no raw seed text reaches
   it. Writer is structurally unable to introduce a duck if the ledger
   contains no duck. `generate_narrative` and
   `generate_narrative_stream` gain a required `ledger_client` kwarg;
   the CLI, web pipeline, and eval runner all build two
   `AnthropicClient` instances per render. Renderers unchanged.
10. [ADR-010 — photo grounding via Claude vision](docs/adr/010-photo-grounding-via-vision.md):
    Phase 3. Per-photo vision describer produces a typed
    `PhotoDescription` (people, objects, location/season clues, body
    language); descriptions flow into the ledger extractor as a new
    `{photo_descriptions_json}` placeholder. Writer prompt unchanged —
    photos enrich the ledger, they do not bypass the ADR-009
    fabrication contract. New `complete_vision()` on `AnthropicClient`,
    new `describe_photo` / `describe_photos` in `trailstory/photos.py`,
    new `Settings.vision_model` + `use_photo_grounding`. Three clients
    per render (writer + ledger + vision) plumbed through the CLI, web,
    eval runner, and fake-LLM dev mode. Per-photo failures soft-fail.
11. [ADR-011 — verifier loop using self-reported provenance](docs/adr/011-verifier-loop-self-reported-provenance.md):
    Phase 2.5. After the writer pass returns, if the writer-self-reported
    INFERRED-sentence share exceeds `Settings.max_inferred_ratio`
    (default `0.5`), regenerate once with feedback. Free signal — no
    extra LLM call to detect, only the conditional regen. "Improvement
    only" admission policy keeps the regen only if its ratio actually
    dropped. Streaming bypassed.
12. [ADR-012 — per-photo vision description cache](docs/adr/012-per-photo-vision-cache.md):
    Phase 3.1. On-disk cache for `PhotoDescription` keyed by
    `(photo bytes SHA-256, vision_model)`. Mirrors the existing
    narrative cache; lives in `~/.cache/trailstory/vision/`.
13. [ADR-013 — parallel vision describer calls](docs/adr/013-parallel-vision-via-threadpool.md):
    Phase 3.2. `describe_photos` uses `ThreadPoolExecutor` with
    `Settings.vision_concurrency` workers. 6-photo hike drops from
    ~6s serial to ~1.5s parallel. Order preserved via `.map`;
    single-photo fast path skips the pool.
14. [ADR-014 — sentence-level provenance + HTML hover](docs/adr/014-sentence-level-provenance-and-html-hover.md):
    Phase 4. `NarrativeOutput.paragraphs` becomes
    `list[Paragraph] = list[list[Sentence]]`; each sentence has
    tri-lingual text + one `Provenance` (source: SEED / PHOTO / GPX /
    INFERRED). Editorial template wraps each sentence in
    `<span class="sent" data-prov="...">` with hover tooltip + tint
    on INFERRED. Log and Encyclopedia templates use the
    `paragraphs_as_localized()` flat fallback until Phase 4.1 ports
    them. `schema_version=3`.
15. [ADR-015 — chapter-based narrative](docs/adr/015-chapter-based-narrative.md):
    `NarrativeOutput.chapters: list[Chapter]` (exactly 6) replaces
    flat `paragraphs` + `selected_photo_indices` as canonical state.
    Each chapter envelope binds one photo and carries its own
    `id` + `time` + `place` + `lat`/`lon` + tri-lingual `title` +
    sentence-leveled `body`. `paragraphs_as_localized()` becomes a
    computed view over `chapters[*].body`; `selected_photo_indices`
    survives as a read-only `@property`. The Trailpath layouts
    (Zine / Sunday / Postcard / Album) consume this shape natively
    in subsequent renderer PRs; the Letter template walks chapter
    bodies with no visible-output change. `Style.log` and
    `Style.encyclopedia` removed — the v0 product decision is to
    ship the five Trailpath styles only. `schema_version=4`.

If you're about to do something that touches an area covered by an existing
ADR, **read the ADR first**. If the change is incompatible with the recorded
decision, open a new ADR rather than silently overriding it.

---

## Getting help

- Architecture questions → open a `docs/` PR with a proposed ADR in `docs/adr/`.
- Prompt quality issues → open an issue using the **Narrative quality**
  template (label `llm`) — it captures the seed text, GPX summary, and the
  expected-vs-actual snippet the rubric/judge will need.
- Bugs → use the **Bug report** template (label `type: fix`).
- Design questions about the HTML template → use the **Feature request**
  template (label `type: feature`).
