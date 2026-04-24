## What does this PR do?

<!-- One paragraph. What problem does it solve, or what feature does it add? -->



## Why this approach?

<!-- If there were alternatives, briefly explain why you chose this one. Skip if obvious. -->



## How to test it

<!-- Step-by-step instructions for a reviewer to verify the change. -->

```bash
# Example:
make dev
trailstory generate --photos ./tests/fixtures/sample_photos --gpx ./tests/fixtures/sample.gpx --seed "..."
```



## Checklist

- [ ] `make ci` passes locally (lint + type check + tests)
- [ ] New behaviour has tests
- [ ] New public functions/classes have docstrings and type hints
- [ ] If this adds a new LLM prompt: the prompt is in `llm/prompts.py`, not inline
- [ ] If this changes the output schema: `models.py` was updated first
- [ ] If this changes the HTML template: `make test-render` was run and output looks correct
- [ ] No secrets, API keys, or personal data committed
- [ ] `CHANGELOG.md` updated (for user-facing changes)

## Related issues

Closes # <!-- issue number, or delete this line -->
