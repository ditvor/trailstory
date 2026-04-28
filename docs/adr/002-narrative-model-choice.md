# ADR 002 — Default narrative model is `claude-opus-4-7`

**Date:** 2026-04
**Status:** Accepted
**Decided by:** initial product owner

---

## Context

The narrative is the single user-facing creative output of Trailstory. For each
generated memory page we make exactly one LLM call to produce a bilingual
narrative (English + Russian) that is then read by family members — including
grandparents in Russia for whom the Russian translation is the only version they
ever see.

When the project was scaffolded, three places named different models:

| Layer | Model | Reason recorded |
|---|---|---|
| `config.py` (`Settings.model` default) | `claude-sonnet-4-6` | matched the rule in CLAUDE.md |
| `llm/client.py` (`DEFAULT_MODEL`) | `claude-opus-4-6` | inline comment justifying opus on quality grounds |
| `.env.example` (commented) | `claude-sonnet-4-6` | followed `config.py` |

The two layers contradicted each other in practice: the CLI passed
`settings.model` to `AnthropicClient`, so the actual call defaulted to sonnet,
while a developer reading `client.py` in isolation would assume opus. PR #16's
description flagged this as an open question to settle with an ADR.

We also have a fresher Claude family available (`claude-opus-4-7`,
`claude-sonnet-4-6`, `claude-haiku-4-5-…`) than the model IDs that were named
in the original code, so the decision is genuinely between **the latest opus**
and **the latest sonnet** rather than between two old IDs.

---

## Options considered

### Option A — `claude-opus-4-7` as the default (chosen)

Latest opus, the highest-quality model in the family. CLI uses
`settings.model` so an environment variable override is one line away.

**Pros:**
- Best output quality on the dimension that matters most for this product:
  literary phrasing, in two languages, on a single shot.
- Russian translations need subtle phrasing — opus has the largest gap over
  sonnet here.
- Single call per hike. The marginal cost difference vs sonnet is on the order
  of cents per memory; for a personal-use family tool that is invisible.
- Resolves the silent code/spec contradiction — `config.py` and
  `client.py` agree on the same string.

**Cons:**
- Higher per-call latency (a few extra seconds — fine for a CLI tool with a
  spinner).
- Higher absolute cost. Acceptable at this volume; revisit if usage grows.

### Option B — `claude-sonnet-4-6` as the default

Cheaper, faster. What CLAUDE.md originally prescribed.

**Pros:** Lower cost, lower latency.
**Cons:** Demonstrably weaker on creative + multilingual prose. Wrong default
for a tool whose entire output is a 5-paragraph piece of writing read by
people who care about it.

### Option C — keep the contradiction, document it as "client knows best"

Leave `client.py` defaulting to opus and `config.py` defaulting to sonnet, on
the theory that the SDK-level constant captures intent and the env-driven
constant captures override.

**Pros:** No code change.
**Cons:** Two sources of truth that disagree, with the actually-effective one
being the cheaper (worse) of the two by accident. Future readers will hit the
same trap PR #16 surfaced.

---

## Decision

**Use `claude-opus-4-7` as the default.** Both `Settings.model` and
`DEFAULT_MODEL` are aligned to this string. Cost-conscious users can downgrade
per-run by setting `MODEL=claude-sonnet-4-6` (or any other model ID the
Anthropic API accepts) in their `.env`.

The CLAUDE.md "Model to use" section is rewritten to make the new default
explicit, and to require a fresh ADR for any future change to the default.

---

## Consequences

- A `make generate` against the bundled fixtures now costs roughly $0.10–$0.30
  per run (vs ~$0.02–$0.05 on sonnet). Acceptable at personal-use volume.
- The two-source contradiction is gone; readers of either file see the same
  model.
- We have written evidence ("the narrative is the single user-facing creative
  output, read by family who care") that explains the choice — anyone proposing
  to change the default has to engage with that argument, not just rewrite the
  string.

### Follow-up to revisit (not blocking)

After ~10 real hikes generated with opus-4-7, do a side-by-side: run the same
hike on sonnet and compare the narrative quality (especially the Russian
translation). If sonnet is indistinguishable on the cases that matter, write
ADR-003 to lower the default. If opus is clearly better, this ADR remains.
