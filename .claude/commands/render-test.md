---
description: Render `templates/memory.html.j2` with fixture data (`make test-render`) and print the output path
---

# /render-test — render the HTML template with fixtures

Build a test HTML from `templates/memory.html.j2` using the bundled GPX/photo
fixtures and a stub `NarrativeOutput`. **No API call.** Useful when iterating
on the template, CSS, or elevation-profile SVG.

## Steps

1. Run `make test-render`. This calls
   `tests.conftest.render_with_fixtures()` which writes the file under
   `output/test/` (see `Makefile` and `tests/conftest.py`).
2. Capture stdout and resolve the absolute path of the produced HTML. Prefer
   reading the path the helper prints; if it is silent, find the most recently
   modified file under `output/test/` with `find output/test -name '*.html' -type f -print0 | xargs -0 ls -t | head -1`.
3. Print the absolute path on its own line so the user can click or copy it.
4. On macOS, suggest opening the file directly:
   ```
   open <absolute-path>
   ```
   On Linux, suggest `xdg-open <absolute-path>`.
5. If the user has just changed `templates/memory.html.j2` or
   `trailstory/renderers/html.py`, remind them: visual diffs aren't covered by
   `make ci`, so eyeball the page before opening a PR.

## Notes

- This command does NOT call the Anthropic API — `render_with_fixtures()`
  uses a hand-written stub `NarrativeOutput`.
- Output directory `output/` is gitignored; the file is for local inspection
  only.
- If the helper raises (e.g. fixture mismatch after a `NarrativeOutput` schema
  change), surface the traceback and remind the user that
  `tests/conftest.py::render_with_fixtures` is part of the contract that needs
  updating alongside the model.
