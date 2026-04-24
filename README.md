# Trailstory

> Turn a hike into a memory worth keeping — and sharing.

[![CI](https://github.com/YOUR_USERNAME/trailstory/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/trailstory/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

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
# 1. Clone and install
git clone https://github.com/YOUR_USERNAME/trailstory.git
cd trailstory
make dev

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

## Development

```bash
make dev        # install in editable mode with all dev deps
make test       # run tests with coverage
make lint       # ruff check
make format     # ruff check --fix
make ci         # full CI check (lint + type check + tests)
```

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
