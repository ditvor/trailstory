# Trailstory

> Turn a hike into a memory worth keeping — and sharing.

[![CI](https://github.com/ditvor/trailstory/actions/workflows/ci.yml/badge.svg)](https://github.com/ditvor/trailstory/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> **Status — early but usable.** The end-to-end pipeline is wired up: `trailstory
> generate` produces a self-contained HTML page and (with `--instagram`) a 1080×1350
> carousel from real GPX + photos + a seed sentence. The narrative is content-cached
> so iterating on the renderer doesn't re-spend an Opus call. Quality is guarded by a
> two-layer eval suite — a free programmatic rubric and a paid LLM-as-judge — covered
> in the [Quality](#quality--how-we-keep-the-prose-good) section below. Rough edges
> remain (location auto-detection, WhatsApp draft generation, real-hike fixture
> coverage); track progress on the
> [pull requests page](https://github.com/ditvor/trailstory/pulls?q=is%3Apr+is%3Aclosed).

---

## The problem

After a hike — especially one that matters — you have 40 photos, a GPX file, and a feeling you want to share. But turning that into something worth sending takes hours: writing, translating, resizing, copying across platforms. Most of the time, it just doesn't happen.

This is worse when your family is far away and on different platforms. When Instagram is blocked in Russia, you can't just share a link. When you're writing in two languages, every edit is twice the work.

Trailstory solves this with a single command. You give it your photos, your GPX file, and two sentences about how it felt. It gives you back a beautiful, self-contained HTML memory page in English and Russian — ready to share as a link, as a file, or as an Instagram carousel. No app. No account. No duplication.

This is not a fitness tracker. It doesn't care about your pace. It cares about the story.

---

## How it works

```
trailstory generate \
  --photos  ./photos/herzogstand \
  --gpx     ./tracks/herzogstand.gpx \
  --seed    "She slept through the whole climb and woke up directly into the Alps." \
  --name    Mia \
  --age     5
```

**Three steps happen automatically:**

1. **Parse** — GPX data is extracted (distance, elevation profile, place context). Photos are sorted by timestamp.
2. **Generate** — Claude reads your seed sentence, the route data, and the photo list. It writes a bilingual narrative and selects the 6–8 photos that best tell the arc.
3. **Render** — A single self-contained HTML file is produced: beautiful typography, embedded photos, elevation profile, language toggle, share buttons. Also outputs an Instagram carousel (optional).

The HTML page works in any browser, offline, without any CDN — which means it works in Russia sent via WhatsApp, WeChat, or email, as well as on Instagram.

---

## Quick start

```bash
# 1. Clone and set up (creates .venv, installs deps, installs git hooks)
git clone https://github.com/ditvor/trailstory.git
cd trailstory
make setup
source .venv/bin/activate

# 2. Set your API key
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY

# 3. Generate your first memory
trailstory generate \
  --photos ./tests/fixtures/sample_photos \
  --gpx    ./tests/fixtures/sample.gpx \
  --seed   "The fog cleared just as we reached the ridge." \
  --out    ./output
```

The output directory will contain `{location}-{date}.html` and (if `--instagram` flag is passed) a `carousel/` folder with numbered images.

---

## Output

| Format | Description | Share via |
|--------|-------------|-----------|
| `.html` | Self-contained page, all photos embedded as base64 | Any link, email, file |
| `carousel/*.jpg` | 5–9 images at 4:5 for Instagram | Instagram upload |
| Console | WhatsApp message draft (EN + RU) | Copy-paste |

---

## Architecture

```
trailstory/
├── cli.py            Entry point — Click commands
├── config.py         Settings via pydantic-settings (.env)
├── models.py         All Pydantic data models (source of truth)
├── gpx.py            GPX parsing → GpxStats + elevation profile
├── photos.py         Photo loading, EXIF sort, resize
├── llm/
│   ├── client.py     Anthropic API wrapper with retry logic
│   ├── prompts.py    All prompt strings (never scattered in code)
│   └── narrative.py  LLM call orchestration → NarrativeOutput
└── renderers/
    ├── html.py       Jinja2 template → .html file
    └── instagram.py  Pillow → carousel images
templates/
└── memory.html.j2    The shareable memory page template
```

Key decisions and their rationale are documented in [`docs/adr/`](docs/adr/).

---

## Quality — how we keep the prose good

The narrative is the user-facing product. A regression in tone or translation hurts real readers — family in Russia who can't file a bug report. So Trailstory ships with a two-layer evaluation harness in [`tests/eval/`](tests/eval/):

| Layer | What it checks | Cost | Where it runs |
|---|---|---|---|
| **Programmatic rubric** | Schema round-trip, paragraph counts, Cyrillic coverage with a mid-paragraph English-fallback guard, EN/RU word-count ratio, length caps on titles and milestones, photo-index validity, and pull-quote provenance vs the body. | Free | `make ci` (unit tests) and `make eval` (rubric against real writer output, paid writer call) |
| **LLM-as-judge** | Scores `warmth`, `narrative_arc`, `russian_fidelity`, and `photo_selection_plausibility` on a 0–5 scale, plus 2–4 sentences of free-form `notes` justifying each score. | Paid | `make eval-live` only |

The judge runs on **a different model from the writer** — `claude-sonnet-4-6` judging `claude-opus-4-7` output by default, configurable via `EVAL_JUDGE_MODEL`. Same-family judging inflates scores; a different perspective is more honest, and Sonnet is cheaper than Opus for the pattern-matching-against-rubric task.

### The regression gate

Each fixture case in [`tests/eval/cases/`](tests/eval/cases/) has a saved baseline at `tests/eval/golden/<case>-judge.json`. When you change a prompt and run `make eval-live`, the runner compares the fresh judge scores against the baseline per axis. If any axis drops by **≥ 1.0** (override with `EVAL_REGRESSION_THRESHOLD`), the runner exits non-zero and CI is red. The 1.0 default is generous enough to absorb typical sampling jitter (~0.5 per axis) without masking real regressions.

```
                                              ┌─────────────────────┐
                                              │  golden judge file  │
                                              │  warmth         5.0 │
                                              │  arc            5.0 │ ← saved
                                              │  ru_fidelity    4.5 │   from a
                                              │  photo          4.5 │   prior run
                                              └──────────┬──────────┘
                                                         │
   you edit a prompt                                     │ compare
        │                                                ▼
        ▼                                       ┌──────────────────┐
   ┌─────────┐    narrative   ┌─────────┐  fresh│   per-axis Δ     │
   │ WRITER  │ ─────────────► │  JUDGE  │ ─────►│                  │
   │  Opus   │                │ Sonnet  │ scores│  any axis -1.0?  │
   └─────────┘                └─────────┘       └─────────┬────────┘
                                                          │
                                                          ▼
                                                ┌──────────────────┐
                                                │ yes → ✗ fail PR  │
                                                │ no  → ✓ pass PR  │
                                                └──────────────────┘
```

### Workflow when changing a prompt

```bash
make eval                # free-ish: rubric vs real writer output
make eval-live           # paid: rubric + judge with golden delta gate
make eval-update-golden  # paid: refresh narrative AND judge goldens
                         # (run only when a stylistic shift is intentional)
```

When the gate fails, read the judge's `notes` column — they cite specific phrases ("sentences 2–3 use English word order", "summit beat is missing"), which turns an abstract delta into something actionable. Post both score tables in the PR description; the [`Update a prompt`](CLAUDE.md#update-a-prompt) recipe in `CLAUDE.md` walks through it.

Full design and trade-offs (why two layers, why a different judge model, what failure modes to expect) are in [`docs/adr/003-narrative-eval-suite.md`](docs/adr/003-narrative-eval-suite.md).

---

## Development

```bash
make setup          # first time: creates .venv, installs deps + git hooks
source .venv/bin/activate

make format         # auto-fix lint + apply ruff format
make ci             # full CI check: ruff + mypy + pytest (same as GitHub Actions)
make test           # run tests with coverage HTML report

# Narrative-quality gates — see the Quality section above.
# These call the real Anthropic API and cost money; do not run in CI.
make eval                # rubric vs real writer output (paid)
make eval-live           # rubric + LLM-as-judge with golden delta gate (paid)
make eval-update-golden  # refresh narrative AND judge goldens (paid)
```

**Every `git push` triggers `make ci` via the pre-push hook** (installed by `make setup`). Skip only for emergencies with `git push --no-verify` — GitHub CI still runs.

See [CONTRIBUTING.md](CONTRIBUTING.md) for branching rules, commit format, and PR process.

---

## Requirements

- Python 3.12+
- An [Anthropic API key](https://console.anthropic.com/)
- Photos in JPEG or HEIC format (HEIC requires `libheif` system library)

---

## License

MIT — see [LICENSE](LICENSE).

---

*Built in Munich, for family everywhere.*
