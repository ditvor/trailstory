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

## How to make common changes

### Add a new field to the narrative output

1. Add field to `NarrativeOutput` in `models.py` (with English and Russian variants).
2. Update `SYSTEM_NARRATIVE` and `USER_NARRATIVE_TEMPLATE` in `llm/prompts.py` to instruct
   the model to populate it.
3. Add the field reference to `templates/memory.html.j2`.
4. Add a test in `test_narrative.py` asserting the field is present in the mock response.

### Change the output page design

Edit `templates/memory.html.j2`. This is a Jinja2 template. Available context variables:

```
{{ narrative }}     NarrativeOutput object — access as narrative.title_en etc.
{{ stats }}         GpxStats object
{{ photos }}        list of dicts with { 'data_uri': str, 'caption': str, 'index': int }
{{ meta.date }}     formatted hike date
{{ meta.slug }}     url-safe hike identifier
```

After editing, run `make test-render` to produce a test HTML in `output/test/`.

### Add a new output format

1. Create `trailstory/renderers/new_format.py`.
2. It takes a `Memory` object and returns a `Path` to the output file.
3. Register it in `cli.py` as an optional flag: `--instagram`, `--pdf`, etc.
4. Add tests in `tests/test_renderers.py`.

### Update a prompt

1. Edit the constant in `llm/prompts.py`. Leave the old version as a comment with the date.
2. Run `make eval` to exercise the new prompt against every case in
   `tests/eval/cases/`. This calls the real Anthropic API and costs money;
   see `docs/adr/003-narrative-eval-suite.md` for the rationale.
3. Inspect the per-case rubric tables. The runner exits non-zero on any
   failure — investigate before merging. Use `--update-golden` to refresh
   `tests/eval/golden/` once the new output is what you intend.
4. Only merge if the rubric is **non-regressing**: every check that passed
   on `develop` must still pass with the new prompt. If a check now fails,
   either fix the prompt or open a PR that adjusts the rubric with
   reasoning.
5. If the output schema changes, update `NarrativeOutput` first (see above).
6. Open a PR with the label `llm`. Note in the description what problem
   the new prompt solves and paste the eval table.

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

## Getting help

- Architecture questions → open a `docs/` PR with a proposed ADR in `docs/adr/`.
- Prompt quality issues → open an issue with label `llm` including the failing seed text and GPX stats.
- Design questions about the HTML template → open an issue with label `type: feature`.
