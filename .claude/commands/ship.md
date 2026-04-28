---
description: Run CI, draft a Conventional Commits message, commit, push, and open a PR against `develop` — pausing before each destructive step
argument-hint: "[--draft]"
---

# /ship — branch → CI → commit → push → PR (with confirmations)

Take a clean working state on a feature branch and ship it as a PR against
`develop`, following [`CONTRIBUTING.md`](CONTRIBUTING.md). **Pause and confirm
before every destructive or externally-visible action — committing, pushing,
opening the PR.**

> Note: this is the project-local `/ship` for Trailstory. It runs `make ci`
> against the project venv and uses the repo's PR template. It is **not** the
> gstack `/ship` skill. If a user explicitly asks for the gstack version,
> defer to that instead.

## Steps

1. **Sanity-check the working tree.**
   - Run `git status`. Expected state: a non-`develop`/`main` branch, the only
     uncommitted changes are the diff the user actually intends to ship.
   - Run `git branch --show-current`. If it is `develop` or `main`, refuse and
     tell the user to create a feature branch first (`feature/`, `fix/`,
     `chore/`, `docs/` per CONTRIBUTING.md).
   - Run `git diff` and `git diff --staged`. Show a 1-paragraph summary of
     what's about to ship.
   - If anything looks unexpected (untracked files, accidental edits, debug
     prints), surface it and ask before continuing.

2. **Run CI.**
   - Run `make ci`. This is `ruff check` + `ruff format --check` + `mypy
     trailstory/` + `pytest --cov=trailstory --cov-fail-under=80`. The user's
     feedback memory says: **never claim "make ci passes" without actually
     running it.**
   - If anything fails, stop. Report the failure and let the user fix it. Do
     not commit.

3. **Draft a Conventional Commits message** from the diff.
   - Subject: 50 chars max, imperative, no trailing period.
   - Type: `feat` / `fix` / `chore` / `docs` / `test` / `refactor` / `perf` /
     `style` (see CONTRIBUTING.md).
   - Scope (optional): `gpx` / `photos` / `llm` / `html` / `instagram` / `cli`
     / `config` / `ci` / `deps` / `eval` / `claude-code` / etc.
   - Body explains WHY, not WHAT.
   - **Show the drafted message to the user and ask them to approve / edit
     before committing.**

4. **Commit.**
   - `git add` only files the user intended (avoid `git add -A` / `git add .`
     so secrets and stray artefacts don't sneak in).
   - Commit with the approved message via heredoc. Append the trailer
     `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
   - Re-run `git status` to confirm the commit landed.
   - If a pre-commit hook fails, **fix the underlying issue and create a NEW
     commit** — do not `--amend` and do not `--no-verify`.

5. **Push.**
   - Confirm with the user one more time before pushing.
   - `git push -u origin <branch-name>`.

6. **Open the PR against `develop`.**
   - Use `gh pr create --base develop --head <branch>` with the project PR
     template body, filled in from the diff and the commit message:
     - `## What does this PR do?` — restate the why in 1-3 sentences.
     - `## Why this approach?` — only if non-obvious.
     - `## How to test it` — concrete commands the reviewer can run.
     - `## Checklist` — tick the boxes that actually apply (CI, tests,
       docstrings, prompt-in-`prompts.py`, model update first, render check,
       no secrets, CHANGELOG).
     - `## Related issues` — fill in `Closes #N` if applicable, otherwise
       delete the line.
   - If `--draft` was passed, add `--draft` to `gh pr create`.
   - Pass the body via heredoc to preserve formatting:
     ```bash
     gh pr create --base develop --head <branch> --title "<conv-commits>" --body "$(cat <<'EOF'
     ...
     EOF
     )"
     ```
   - Print the PR URL on success.

## Notes

- Never force-push, never push to `main` or `develop` directly.
- Never bypass hooks (`--no-verify`) — investigate failures instead.
- For LLM/prompt-touching PRs, remind the user to paste rubric + judge tables
  in the PR body (see `/eval`, `/eval-live`).
- For HTML template changes, remind the user to run `/render-test` and
  visually verify before pushing.
