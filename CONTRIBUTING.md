# Contributing to Trailstory

This document defines how we work. Its purpose is to make the codebase
predictable — so that six months from now, reading any commit, branch name,
or PR description tells you exactly what happened and why.

---

## Table of contents

- [Branching strategy](#branching-strategy)
- [Commit messages](#commit-messages)
- [Pull requests](#pull-requests)
- [Code review](#code-review)
- [Branch protection rules](#branch-protection-rules)
- [Code quality standards](#code-quality-standards)
- [Dependency management](#dependency-management)

---

## Branching strategy

We use a lightweight trunk-based model with a staging buffer.

```
main ─────────────────────────────────────────────── production-ready, tagged releases
  └─ develop ────────────────────────────────────── integration branch, always green CI
       ├─ feature/42-instagram-carousel
       ├─ fix/38-heic-exif-timezone
       ├─ chore/update-anthropic-sdk
       └─ docs/adr-photo-embed-strategy
```

### Branch types

| Prefix | Purpose | Merges into | Example |
|--------|---------|-------------|---------|
| `feature/` | New capability | `develop` | `feature/14-elevation-svg` |
| `fix/` | Bug fix | `develop` | `fix/22-missing-exif-fallback` |
| `chore/` | Deps, CI, refactoring, tooling | `develop` | `chore/upgrade-pillow-11` |
| `docs/` | Documentation only, no code change | `develop` | `docs/adr-bilingual-output` |
| `hotfix/` | Critical production fix | `main` AND `develop` | `hotfix/api-key-leak` |

### Rules

- **Always branch from `develop`**, not `main` (except `hotfix/`).
- **Branch names are lowercase and hyphen-separated.** No underscores, no camelCase.
- **Include the issue number** when one exists: `feature/42-instagram-carousel`.
- **Delete the branch** after it is merged. Keep the remote clean.
- **Never commit directly** to `main` or `develop`. Always via PR.
- `main` only receives merges from `develop` (scheduled releases) or `hotfix/` branches.

### Naming examples

```bash
# Good
git checkout -b feature/14-gpx-elevation-profile
git checkout -b fix/27-photo-sort-timezone
git checkout -b chore/add-mypy-config
git checkout -b docs/update-quickstart

# Bad
git checkout -b my-feature
git checkout -b Feature_14
git checkout -b fixBug
```

---

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/). Every commit message
must be parseable by a machine and readable by a human.

### Format

```
<type>(<scope>): <short description>

[optional body — wrap at 72 chars]

[optional footer: BREAKING CHANGE or Closes #N]
```

### Types

| Type | When to use |
|------|-------------|
| `feat` | New feature visible to the user |
| `fix` | Bug fix |
| `chore` | Build, deps, CI — no user-facing change |
| `docs` | Documentation only |
| `test` | Adding or fixing tests only |
| `refactor` | Code change that is neither a fix nor a feature |
| `perf` | Performance improvement |
| `style` | Formatting only (whitespace, commas) — no logic change |

### Scopes (optional but recommended)

`gpx`, `photos`, `llm`, `html`, `instagram`, `cli`, `config`, `ci`, `deps`

### Examples

```
feat(llm): add photo selection prompt with arc-based guidance

fix(photos): fall back to file mtime when EXIF timestamp is missing

chore(deps): upgrade anthropic SDK to 0.40.0

test(gpx): add fixture for GPX file with no elevation data

docs(adr): record decision to embed photos as base64

feat(html): add RU/EN language toggle to memory page template

BREAKING CHANGE: NarrativeOutput now requires title_ru field
```

### Rules

- **Subject line: 50 chars max**, imperative mood ("add X", not "added X" or "adding X").
- **No period** at the end of the subject line.
- **Body explains WHY**, not what (the diff already shows what).
- **One logical change per commit.** If you have to use "and" in the subject, split it.

---

## Pull requests

### Before opening a PR

1. Your branch has a passing CI run.
2. You've run `make ci` locally without errors.
3. New code has tests. New features have documentation.
4. The PR does one thing. If it grew into two things, split it.

### PR title

Follow the same Conventional Commits format as commit messages.

```
feat(llm): implement photo selection with narrative arc guidance
fix(photos): handle HEIC files without EXIF metadata
chore: upgrade to Python 3.13
```

### PR description

The repository contains a [PR template](.github/PULL_REQUEST_TEMPLATE.md) that loads automatically.
Fill it in — do not delete sections.

### PR size guidelines

| Lines changed | Verdict |
|---------------|---------|
| < 200 | Ideal — review in under 15 min |
| 200–500 | Acceptable — add extra context in description |
| > 500 | Split it unless it is a single atomic change (e.g. generated file) |

### Draft PRs

Open as **Draft** if you want early feedback or CI runs before the code is ready for review.
Never request review on a Draft PR.

### Merge strategy

- PRs into `develop`: **Squash and merge** — keeps history linear.
- `develop` into `main`: **Merge commit** — preserves the integration history.
- `hotfix/` into `main`: **Merge commit**, then immediately merge `main` back into `develop`.

---

## Code review

### Reviewer responsibility

A review approval means: *"I have read this, I understand it, and I am comfortable with it
shipping."* Not: *"It looks fine, I skimmed it."*

### What to check

- Does it do what the PR description says?
- Are there tests? Do they test the right thing?
- Are new Pydantic models correctly typed?
- Are new LLM prompts in `llm/prompts.py` (not scattered in code)?
- Does it handle the failure cases (missing EXIF, API timeout, malformed GPX)?
- Does it log API keys, user data, or file paths inappropriately?

### Review vocabulary

Use these prefixes to signal intent:

| Prefix | Meaning |
|--------|---------|
| `nit:` | Minor style preference — author decides, no action required |
| `suggestion:` | Better approach exists — worth considering, not blocking |
| `question:` | Needs clarification before approval |
| `blocking:` | Must be resolved before merge |

```
nit: this variable name could be more descriptive
suggestion: consider using a generator here to avoid loading all photos into memory
question: does this handle GPX files with no trackpoints?
blocking: this logs the API key on line 42
```

---

## Branch protection rules

Configure these in GitHub → Settings → Branches.

### `main`

| Rule | Setting |
|------|---------|
| Require PR before merging | ✅ |
| Required approvals | 1 |
| Dismiss stale reviews on new commits | ✅ |
| Require status checks to pass | ✅ `ci / quality` |
| Require branches to be up to date | ✅ |
| Do not allow bypassing above settings | ✅ |
| Allow force pushes | ❌ |
| Allow deletions | ❌ |

### `develop`

| Rule | Setting |
|------|---------|
| Require PR before merging | ✅ |
| Required approvals | 1 |
| Require status checks to pass | ✅ `ci / quality` |
| Allow force pushes | ❌ |
| Allow deletions | ❌ |

The `scripts/setup_github.sh` script configures these automatically via the GitHub CLI.

---

## Code quality standards

### Tooling

| Tool | Purpose | Config |
|------|---------|--------|
| `ruff` | Lint + format | `pyproject.toml` |
| `mypy` | Static type checking | `pyproject.toml` |
| `pytest` | Tests + coverage | `pyproject.toml` |

### Requirements

- **Type hints on all public functions and class attributes.**
- **Docstrings on all public functions and classes.** One-line is fine for simple functions.
- **Coverage ≥ 80%** on `trailstory/` (excluding `cli.py`). CI enforces this.
- **No `# type: ignore`** without an accompanying comment explaining why.
- **No bare `except:`** — always catch a specific exception type.

### Running checks locally

```bash
make lint       # ruff check .
make format     # ruff check --fix . && ruff format .
make typecheck  # mypy trailstory/
make test       # pytest --cov=trailstory
make ci         # all of the above in sequence
```

---

## Dependency management

- All dependencies are declared in `pyproject.toml`.
- Pin versions for reproducibility: use `>=X.Y, <X+1` ranges, not `*`.
- **Never use `pip install <package>` without adding it to `pyproject.toml`.**
- Dev dependencies (ruff, mypy, pytest) go in `[project.optional-dependencies] dev`.
- When upgrading a major version, open a dedicated `chore/` PR.

---

## Labels

The setup script creates these labels on the repository:

| Label | Colour | Meaning |
|-------|--------|---------|
| `type: feature` | `#0075ca` | New capability |
| `type: fix` | `#d93f0b` | Bug fix |
| `type: chore` | `#e4e669` | Maintenance |
| `type: docs` | `#0052cc` | Documentation |
| `priority: high` | `#b60205` | Blocking or urgent |
| `priority: low` | `#cfd3d7` | Nice to have |
| `needs: review` | `#fbca04` | Ready for review |
| `needs: tests` | `#e11d48` | Missing test coverage |
| `llm` | `#7c3aed` | Relates to prompt or LLM logic |
