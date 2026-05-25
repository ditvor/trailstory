"""Development helpers for click-through UI testing.

When ``WEB_FAKE_LLM=1`` (or ``python -m web --fake-llm``) is set,
``web.__main__`` swaps the real LLM client factory for
:func:`make_fake_client_factory`, which returns a deterministic
``MagicMock``-shaped client. The full pipeline then runs end-to-end —
GPX parse, photo load + resize, narrative generation, HTML render — but
without paying for any Anthropic API calls.

The fake narrative is intentionally a constant (the same EN/RU/DE
fixture used by the test suite) so iterating on the form, the privacy
page, or the output template is fast and reproducible. It is **not**
meant for any kind of perf or correctness testing of the LLM layer —
the eval suite (``make eval`` / ``make eval-live``) is the place for
that.

Not loaded in the production code path: the env flag has to be set
explicitly, and even then this module's only side effect is producing
a configured ``MagicMock``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Final
from unittest.mock import MagicMock

from trailstory.llm.client import AnthropicClient

logger = logging.getLogger(__name__)

# 8 indices so any reasonable upload (the narrative prompt asks for
# 6-8) finds room. The pipeline filters out indices past the actual
# photo count, so a hike with 3 photos still gets a usable selection.
_FAKE_NARRATIVE: Final[str] = json.dumps(
    {
        "schema_version": 2,
        "title": {
            "en": "Above the fog line",
            "ru": "Над линией тумана",
            "de": "Über der Nebelgrenze",
        },
        "subtitle": {
            "en": "A morning above the cloud sea",
            "ru": "Утро над морем облаков",
            "de": "Ein Morgen über dem Wolkenmeer",
        },
        "paragraphs": {
            "en": [
                "We left the trailhead at first light, the air sharp with damp moss.",
                "By the saddle the cloud was thinning into a soft white scarf.",
                "At the ridge the sun broke through and the valley vanished beneath us.",
            ],
            "ru": [
                "Вышли на тропу с первыми лучами; воздух пах мхом и хвоей.",  # noqa: RUF001
                "К седловине облака уже редели, превращаясь в белый шарф.",  # noqa: RUF001
                "На хребте солнце пробилось сквозь туман — долина исчезла под нами.",  # noqa: RUF001
            ],
            "de": [
                "Bei erstem Licht brachen wir auf, die Luft scharf von feuchtem Moos.",
                "Am Sattel zog die Wolke sich zu einem weichen weißen Schal zusammen.",
                "Am Grat brach die Sonne durch — das Tal verschwand unter uns.",
            ],
        },
        "pull_quote": {
            "en": "The fog cleared just as we reached the ridge.",
            "ru": "Туман рассеялся как раз когда мы вышли на хребет.",
            "de": "Der Nebel lichtete sich, gerade als wir den Grat erreichten.",
        },
        "milestone": {
            "en": "First time above the fog",
            "ru": "Впервые над туманом",
            "de": "Erstes Mal über dem Nebel",
        },
        "selected_photo_indices": [0, 1, 2, 3, 4, 5, 6, 7],
    }
)


def make_fake_client_factory() -> Callable[[], AnthropicClient]:
    """Return a factory that produces a deterministic fake LLM client.

    The factory shape matches the real one in ``web.app`` so swapping
    is a one-line change at app construction. The mock supports both
    the synchronous ``complete`` path (CLI / non-streaming generation)
    and the ``complete_stream`` path used by the SSE flow — the stream
    method yields the same constant narrative split across a handful
    of chunks so the dev page shows the streaming animation.
    """

    def _factory() -> AnthropicClient:
        fake = MagicMock(spec=AnthropicClient)
        # ``.model`` is a real string because the cache key uses it as
        # a dict value; not strictly needed when ``use_cache=False``
        # but cheap insurance against the cache path getting wired
        # in by mistake.
        fake.model = "trailstory-dev-fake"
        fake.complete.return_value = _FAKE_NARRATIVE

        def _stream(*_args: object, **_kwargs: object) -> object:
            # Slice the fixture into evenly-sized chunks so the SSE
            # flow has multiple frames to render.
            step = max(1, len(_FAKE_NARRATIVE) // 16)
            return iter(_FAKE_NARRATIVE[i : i + step] for i in range(0, len(_FAKE_NARRATIVE), step))

        fake.complete_stream.side_effect = _stream
        return fake

    return _factory


# Deterministic FactLedger fixture for the ADR-009 extractor pass in
# fake-LLM dev mode. Matches the prose in _FAKE_NARRATIVE — the chronology
# beats line up with the three "above the fog line" paragraphs so the
# round-trip looks coherent in click-through UI testing.
_FAKE_LEDGER_EXTRACTOR_OUTPUT: Final[str] = json.dumps(
    {
        "people": [],
        "weather": "fog clearing to sun",
        "chronology": [
            {
                "time_of_day": "first light",
                "activity": "leaving the trailhead through damp moss",
                "emotion": "anticipation",
                "objects_mentioned": ["moss"],
            },
            {
                "time_of_day": "morning",
                "activity": "ascent toward the saddle through thinning cloud",
                "emotion": "focused",
                "objects_mentioned": ["cloud", "saddle"],
            },
            {
                "time_of_day": "midday",
                "activity": "the ridge moment — sun breaking, valley vanishing",
                "emotion": "awe",
                "objects_mentioned": ["ridge", "sun", "valley"],
            },
        ],
    }
)


def make_fake_ledger_client_factory() -> Callable[[], AnthropicClient]:
    """Return a factory for the ADR-009 extractor pass in fake-LLM dev mode.

    Mirrors :func:`make_fake_client_factory` for the second client the
    two-pass narrative pipeline needs. Returns a deterministic ledger so
    the dev UI exercises the same end-to-end shape real users see, with
    no Anthropic API calls.
    """

    def _factory() -> AnthropicClient:
        fake = MagicMock(spec=AnthropicClient)
        fake.model = "trailstory-dev-fake-ledger"
        fake.complete.return_value = _FAKE_LEDGER_EXTRACTOR_OUTPUT
        return fake

    return _factory


# Deterministic PhotoDescription fixture for the ADR-010 vision pass in
# fake-LLM dev mode. Same constant for every photo — the dev UI doesn't
# exercise per-photo variation, only the round-trip shape.
_FAKE_PHOTO_DESCRIPTION: Final[str] = json.dumps(
    {
        "people_visible": ["a person in a blue jacket"],
        "objects_visible": ["a wooden path"],
        "location_clues": ["forest with tall pines"],
        "season_clues": ["bright midday light"],
        "body_language_notes": ["walking forward, relaxed posture"],
    }
)


def make_fake_vision_client_factory() -> Callable[[], AnthropicClient]:
    """Return a factory for the ADR-010 per-photo vision pass in fake-LLM dev mode.

    The vision client uses ``complete_vision()`` (not ``complete()``);
    this mock serves the same deterministic ``PhotoDescription`` JSON for
    every photo so the dev pipeline exercises the round-trip without
    Anthropic API calls or actual image processing.
    """

    def _factory() -> AnthropicClient:
        fake = MagicMock(spec=AnthropicClient)
        fake.model = "trailstory-dev-fake-vision"
        fake.complete_vision.return_value = _FAKE_PHOTO_DESCRIPTION
        return fake

    return _factory


def banner() -> str:
    """Short banner printed when the dev mode is active."""
    return (
        "WEB_FAKE_LLM=1 — using deterministic fake LLM client. "
        "Generated narratives will all be the same fixture text. "
        "Use this only for click-through UI testing."
    )
