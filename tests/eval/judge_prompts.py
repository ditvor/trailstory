"""Prompt templates for the paid LLM-as-judge eval layer.

Mirrors the convention in :mod:`trailstory.llm.prompts`: every prompt
string in the codebase is a module-level constant, no logic, no helper
functions. The runner in :mod:`tests.eval.run` (when ``--live-judge`` is
set) and :func:`tests.eval.judge.judge_narrative` fill the placeholders
via ``str.format(**fields)``.

When you change a prompt:

1. Leave the previous version commented above with a date, so prompt
   evolution is auditable in git history.
2. If the JSON skeleton embedded in ``USER_JUDGE_TEMPLATE`` no longer
   matches :class:`tests.eval.judge.JudgeScore`, update the model first,
   then this file. The drift test in ``tests/test_eval_judge_prompts.py``
   fails loudly if these go out of sync.
"""

from __future__ import annotations

# ── SYSTEM_JUDGE ─────────────────────────────────────────────────────────────
#
# Previous version (pre-2026-04). Bilingual EN/RU framing.
# Kept for revertability while the tri-lingual variant settles.
#
# SYSTEM_JUDGE = """\
# You are a strict literary critic scoring family hiking memories on a fixed rubric.
# You read English and Russian fluently and can compare a translation to its source.
# Score each axis on the 0-5 scale defined in the user message; do not invent axes.
# Be conservative: 5 is reserved for output you would publish without edits.
# Always output valid JSON matching the JudgeScore schema. No prose, no markdown fences.
# """
#
# Current version (2026-04). Tri-lingual narratives — judge still scores
# EN+RU on `russian_fidelity`; DE is eyeballed manually for v0
# (see ADR-005 follow-up).
SYSTEM_JUDGE: str = """\
You are a strict literary critic scoring hiking memories on a fixed rubric.
You read English and Russian fluently and can compare a translation to its source.
The narrative also includes a German variant; you are not asked to score it on this rubric.
Score each axis on the 0-5 scale defined in the user message; do not invent axes.
Be conservative: 5 is reserved for output you would publish without edits.
Always output valid JSON matching the JudgeScore schema. No prose, no markdown fences.
"""

# ── USER_JUDGE_TEMPLATE ──────────────────────────────────────────────────────
#
# Previous version (pre-2026-04). Inlined ``baby_name`` / ``baby_age_months``
# placeholders. Replaced under ADR-004 (drop baby fields) and ADR-005
# (LocalizedString shape — narrative_json is now hierarchical).
# Kept for revertability.
#
# USER_JUDGE_TEMPLATE = """\
# You are scoring a bilingual hiking-memory narrative produced by another model
# for a family with a young baby. The reader is a grandparent in Russia or a
# friend abroad.
#
# Hike context:
# - Parent's seed text: "{seed_text}"
# - Baby: {baby_name}, {baby_age_months} months old
#
# Narrative under review (full JSON):
# {narrative_json}
# ... (rubric body) ...
# """
#
# Current version (2026-04). Subject-agnostic; tri-lingual narrative_json.
#
# Required placeholders (the orchestrator must supply every one):
#   seed_text, narrative_json
#
# JSON braces in the embedded skeleton are doubled (``{{`` / ``}}``) so
# they survive ``str.format()`` unchanged.
USER_JUDGE_TEMPLATE: str = """\
You are scoring a hiking-memory narrative produced by another model. The
narrative is in English, Russian, and German; you score the English and
Russian variants only. The reader is a close family member or friend.

Hike context:
- Hiker's seed text: "{seed_text}"

Narrative under review (full JSON, with English/Russian/German variants):
{narrative_json}

Rubric — score each axis on a 0-5 float scale (0.5 increments are fine):

- warmth (0-5): how warm, personal, and intimate the prose feels.
  5 = a hiker's own voice, specific sensory detail, no boilerplate.
  3 = readable but generic.
  0 = sporty, achievement-focused, or detached.
- narrative_arc (0-5): does the piece move through opening / effort /
  landscape / a human-detail beat / summit-or-endpoint, with a satisfying shape?
  5 = clear arc, every paragraph earns its place.
  3 = present but uneven (one beat thin, one beat overlong).
  0 = no discernible arc, or paragraphs in arbitrary order.
- russian_fidelity (0-5): does the Russian read as natural, equivalent-register
  Russian — not a literal back-translation, not abbreviated, not English
  grammar in Cyrillic letters?
  5 = a Russian native would not guess it was translated.
  3 = correct but stiff; one or two awkward phrasings.
  0 = mistranslations, missing sentences, or English left in.
- photo_selection_plausibility (0-5): given the narrative arc, do the
  selected_photo_indices look like a coherent 6-8 frame story?
  Distribution across the available range, count in [6, 8], and apparent
  match to the beats described in the prose all matter.
  5 = indices clearly trace the arc.
  3 = plausible but slightly clustered or off-count.
  0 = duplicate, out-of-range, wildly miscounted, or contradicts the prose.

Output only JSON — no markdown fences, no commentary — matching this exact
shape (every field is required):

{{
  "warmth": 0.0,
  "narrative_arc": 0.0,
  "russian_fidelity": 0.0,
  "photo_selection_plausibility": 0.0,
  "notes": "2-4 short sentences justifying the scores; cite specific phrases."
}}
"""

# Suffix appended to the user prompt when the first response failed to
# parse as JSON. The judge runner retries the call once with this
# directive attached. Mirrors ``USER_NARRATIVE_RETRY_SUFFIX`` in
# ``trailstory/llm/prompts.py``.
USER_JUDGE_RETRY_SUFFIX: str = "\n\noutput only valid JSON, no prose"
