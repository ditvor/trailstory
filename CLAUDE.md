# Trailstory — Claude Code context

This file tells you everything you need to know to contribute to this codebase.
Read it before writing any code. It answers: what does this do, how is it structured,
and what are the decisions already made that you must not reverse.

---

## What this project does

Trailstory is a command-line tool that takes hiking inputs (GPX track, photos, a short
emotional description from the parent) and produces a beautiful, shareable memory page.

The core user: a parent with a young infant (currently 5 months old) living in Munich,
who hikes regularly and wants to share those experiences with family in Russia — where
Instagram and some messaging platforms are blocked — and also post on Instagram.

The output is a **single self-contained HTML file** that:
- Works in any browser without internet access or CDN
- Can be sent as a file via WhatsApp, email, or any messenger
- Has a bilingual toggle (English / Russian)
- Contains an embedded elevation profile SVG
- Has share buttons (WhatsApp, copy link, Instagram export prompt)

This is **not** a fitness tracker. It is a family memory tool.
Stats (distance, elevation) appear in the output but serve the narrative, not the other way around.

---

## Glossary

One-liners. When in doubt, this is the meaning the codebase intends.

- **slug** — url-safe hike identifier (e.g. `2025-05-tegernsee-fog`). Available
  inside the template as `{{ meta.slug }}` and used as the output directory
  and HTML filename.
- **narrative** — the LLM-generated content (`NarrativeOutput`): bilingual
  title, subtitle, paragraphs, pull quote, milestone, and selected photo
  indices.
- **subtitle** — the short `subtitle_en` / `subtitle_ru` line under the title.
  Sets the emotional tone in one sentence.
- **pull quote** — the `pull_quote_en` / `pull_quote_ru` callout rendered
  large in the page body. Pulled from the parent's seed text or close to it.
- **milestone** — the `milestone_en` / `milestone_ru` badge ("First mountain
  hike", "First time above the fog").
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

tests/
├── conftest.py         Shared fixtures. Fake API key injection. Sample data.
├── fixtures/
│   ├── sample.gpx      A real-shaped GPX file for testing.
│   └── sample_photos/  5 small JPEG files with EXIF timestamps.
├── test_gpx.py
├── test_photos.py
├── test_narrative.py   Always mocks the Anthropic client. Never calls the real API.
└── test_renderers.py

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
NarrativeOutput         (bilingual title, paragraphs, pull quote, selected_photo_indices)
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
    seed_text: str                      # the parent's 2-3 sentence emotional description
    baby_name: str
    baby_age_months: int
    location_name: str | None           # auto-detected from GPX if not provided

class NarrativeOutput:
    title_en: str
    title_ru: str
    subtitle_en: str
    subtitle_ru: str
    paragraphs_en: list[str]            # 3-5 paragraphs
    paragraphs_ru: list[str]
    pull_quote_en: str
    pull_quote_ru: str
    milestone_en: str                   # e.g. "First mountain hike"
    milestone_ru: str
    selected_photo_indices: list[int]   # 6-8 indices into PhotoMeta list

class Memory:
    hike_input: HikeInput
    gpx_stats: GpxStats
    narrative: NarrativeOutput
    selected_photos: list[PhotoMeta]    # resolved from indices
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

The user provides a directory of photos. The LLM receives a numbered list and selects
6–8 indices that it judges will tell the best narrative arc (start, effort, landscape,
baby detail, summit). The user does not curate.

This is a core UX decision. Do not add a `--select-photos` flag without discussion.

### 3. Bilingual output is the default, not an option

Every `NarrativeOutput` field has `_en` and `_ru` variants. The HTML template always
renders both; the reader toggles via a CSS class switch. There is no `--language` flag.

If you need to add a third language, add it to the model and template in a single PR.
Do not add partial language support.

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

1. Add the field to `NarrativeOutput` in `trailstory/models.py` (English and
   Russian variants if it's user-facing text).
2. Update the JSON skeleton in `llm/prompts.py` so the model is instructed to
   produce it. Leave the previous version as a dated comment.
3. Update mocks: `tests/test_narrative.py`, `tests/test_cli.py`,
   `tests/test_renderers.py`, `tests/test_instagram.py`,
   `tests/conftest.py`. The `render_with_fixtures` helper and any stub
   `NarrativeOutput` constructor must include the new field — otherwise
   `make ci` and `/render-test` will break.
4. Reference the field in `templates/memory.html.j2`.
5. If it appears in the carousel, update `trailstory/renderers/instagram.py`.
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
   `baby_name`, `baby_age_months`, `location_name`.
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

Edit `templates/memory.html.j2`. This is a Jinja2 template. Available
context variables:

```
{{ narrative }}     NarrativeOutput object — access as narrative.title_en etc.
{{ stats }}         GpxStats object
{{ photos }}        list of dicts with { 'data_uri': str, 'caption': str, 'index': int }
{{ meta.date }}     formatted hike date
{{ meta.slug }}     url-safe hike identifier
```

After editing, run `make test-render` (or `/render-test`) to produce a test
HTML in `output/test/` and open it in a browser before opening the PR.

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
