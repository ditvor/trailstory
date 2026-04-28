---
description: Run the programmatic narrative-quality rubric (`make eval`) and summarize results
argument-hint: "[--case <slug>]"
---

# /eval — programmatic narrative rubric

Run the programmatic narrative-quality rubric across every fixture case and report
results in a compact table. **This calls the real Anthropic API for the writer
(narrative generation) — it costs money, but only writer dollars (no judge).**
Documented in [`docs/adr/003-narrative-eval-suite.md`](docs/adr/003-narrative-eval-suite.md).

## Steps

1. Confirm with the user: this hits the real Anthropic API. If they passed an
   argument like `--case <slug>`, run `make eval` for that single case via
   `.venv/bin/python -m tests.eval.run --case <slug>` instead. Otherwise run the
   full suite.
2. Run `make eval`. Stream stdout/stderr so the user sees progress.
3. Parse the per-case rubric output. For each case, extract:
   - case name
   - which checks passed
   - which checks failed (and the failure reason printed by `tests/eval/rubric.py`)
4. Render a single Markdown table: `| case | check | status | note |`. Group by
   case. Use `✓` for pass, `✗` for fail.
5. Echo the runner's exit code at the end.
6. If any check failed:
   - Quote the exact failure messages.
   - Suggest the most likely fix per failing check (e.g. `paragraph_count` →
     adjust the "3–5 paragraphs" instruction in `USER_NARRATIVE_TEMPLATE`;
     `cyrillic_coverage_ru` → reinforce "respond in Russian" in
     `SYSTEM_NARRATIVE`; `indices_valid` → double-check that the prompt asks
     for 6–8 indices and they're 0-based).
   - Do **not** automatically edit prompts or goldens — surface the regression
     and let the user decide whether to fix the prompt or refresh the golden
     via `make eval-update-golden`.
7. Remind the user: per CLAUDE.md, a prompt change must also be checked with
   `/eval-live` (paid judge layer) before merging.

## Notes

- The runner exits non-zero on any failure — preserve that exit code in your
  summary so the user knows the overall verdict.
- Never call the real Anthropic API in unit tests — but `make eval` is the one
  exception by design (see ADR-003).
- If the user is iterating on a single case, prefer `--case <slug>` to avoid
  re-spending writer dollars on cases that aren't moving.
