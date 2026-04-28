---
description: Run rubric + paid LLM-as-judge (`make eval-live`) and confirm before treating any new score as the golden
argument-hint: "[--case <slug>]"
---

# /eval-live — rubric + paid LLM-as-judge

Run the full rubric **plus** the paid LLM-as-judge layer that scores warmth,
narrative arc, Russian fidelity, and photo-selection plausibility. Compares
against `tests/eval/golden/<case>-judge.json` and exits non-zero if any axis
drops by ≥ `EVAL_REGRESSION_THRESHOLD` (default 1.0). Documented in
[`docs/adr/003-narrative-eval-suite.md`](docs/adr/003-narrative-eval-suite.md).

**This is the most expensive eval — it makes both writer and judge calls per
case. Always confirm with the user before running.**

## Steps

1. Tell the user this will make `<n_cases>` writer calls AND `<n_cases>` judge
   calls (writer = `claude-opus-4-7`, judge = `claude-sonnet-4-6` by default).
   Ask them to confirm before proceeding.
2. If they passed `--case <slug>`, scope the run to that case via
   `.venv/bin/python -m tests.eval.run --case <slug> --live-judge`. Otherwise
   run `make eval-live`.
3. Stream the runner output so the user sees progress.
4. Parse the per-case judge table. For each case, extract:
   - per-axis score (warmth, narrative_arc, russian_fidelity,
     photo_selection_plausibility)
   - per-axis golden value (if any)
   - per-axis delta (current − golden)
   - judge `notes` field
5. Render two Markdown tables in your reply:
   - **Rubric** — same format as `/eval` (case × check × status × note).
   - **Judge** — `| case | axis | score | golden | Δ | verdict |`. `verdict` is
     `✓` if Δ ≥ −EVAL_REGRESSION_THRESHOLD (default 1.0), `✗` otherwise.
6. Surface the runner's exit code.
7. **Before treating any score as the new golden:** if any axis dropped or the
   user wants to bless the new output as canonical, **explicitly confirm with
   the user** before suggesting `make eval-update-golden`. Never run
   `make eval-update-golden` automatically — golden changes are a deliberate
   editorial decision and must be reviewed by the human, ideally with the
   reasoning recorded in the PR description.
8. Remind the user: per CLAUDE.md "Update a prompt", both rubric and judge
   tables must be pasted in the PR.

## Notes

- The judge model is configurable via the `EVAL_JUDGE_MODEL` env var.
- A judge axis dropping by < `EVAL_REGRESSION_THRESHOLD` is not a regression
  (within noise floor) — but a steady downward drift across runs is a yellow
  flag worth mentioning to the user.
- Per CLAUDE.md, the PR description for any prompt change must include both
  score tables produced here.
