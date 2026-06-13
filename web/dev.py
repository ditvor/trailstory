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
from trailstory.place import PlaceReference

logger = logging.getLogger(__name__)


# 8 indices so any reasonable upload (the narrative prompt asks for
# 6-8) finds room. The pipeline filters out indices past the actual
# photo count, so a hike with 3 photos still gets a usable selection.
# ADR-014 / Phase 4 paragraphs shape: list of paragraphs, each a list of
# sentences with tri-lingual text + per-sentence provenance.
def _fake_paragraph(en: str, ru: str, de: str, source: str = "seed") -> list[dict[str, object]]:
    return [
        {
            "text": {"en": en, "ru": ru, "de": de},
            "provenance": {"source": source, "reference": "fake-llm dev fixture"},
        }
    ]


_FAKE_NARRATIVE: Final[str] = json.dumps(
    {
        "schema_version": 5,
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
        "paragraphs": [
            _fake_paragraph(
                "We left the trailhead at first light, the air sharp with damp moss.",
                "Вышли на тропу с первыми лучами; воздух пах мхом и хвоей.",  # noqa: RUF001
                "Bei erstem Licht brachen wir auf, die Luft scharf von feuchtem Moos.",
            ),
            _fake_paragraph(
                "By the saddle the cloud was thinning into a soft white scarf.",
                "К седловине облака уже редели, превращаясь в белый шарф.",  # noqa: RUF001
                "Am Sattel zog die Wolke sich zu einem weichen weißen Schal zusammen.",
            ),
            _fake_paragraph(
                "At the ridge the sun broke through and the valley vanished beneath us.",
                "На хребте солнце пробилось сквозь туман — долина исчезла под нами.",  # noqa: RUF001
                "Am Grat brach die Sonne durch — das Tal verschwand unter uns.",
            ),
        ],
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
        # ADR-016 shape exercise. extract_ledger re-verifies these against
        # whatever seed the dev user actually typed, so in fake-LLM mode
        # they usually filter down to [] — which is itself the documented
        # "proceed without" path.
        "verbatim_user_phrases": ["fog cleared just as we reached"],
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
        # ADR-018 enriched fields, so the dev UI exercises the full shape.
        "interactions": [],
        "legible_text": [],
        "scene_type": "forest trail",
        "light_and_color": "bright midday light, green canopy",
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


# Deterministic place-stitch fixture for the ADR-017 place pass in fake-LLM
# dev mode. Shape matches ``trailstory.llm.place._PlaceOutput``.
_FAKE_PLACE_OUTPUT: Final[str] = json.dumps(
    {
        "summary": {
            "en": (
                "Bad Tölz is a market town on the Isar in the Bavarian Prealps; "
                "you walked the river path and passed the old church."
            ),
            "ru": "Бад-Тёльц — городок на Изаре в Баварских предгорьях; вы шли вдоль реки.",
            "de": (
                "Bad Tölz ist eine Marktstadt an der Isar in den Bayerischen "
                "Voralpen; ihr seid am Fluss entlanggegangen."
            ),
        },
        "used_hiker_details": ["the church"],
    }
)


def make_fake_place_client_factory() -> Callable[[], AnthropicClient]:
    """Return a factory for the ADR-017 place-stitch pass in fake-LLM dev mode.

    Mirrors the other fake factories: a ``MagicMock`` whose ``complete``
    returns a constant place-context summary so the dev UI exercises the
    "about this place" block round-trip without an Anthropic call.
    """

    def _factory() -> AnthropicClient:
        fake = MagicMock(spec=AnthropicClient)
        fake.model = "trailstory-dev-fake-place"
        fake.complete.return_value = _FAKE_PLACE_OUTPUT
        return fake

    return _factory


def fake_place_reference_resolver(
    lat: float, lon: float, location_name: str | None = None
) -> PlaceReference:
    """Offline stand-in for ``resolve_place_reference`` in fake-LLM dev mode.

    The geocode + Wikipedia lookup is real network even when the LLM is
    faked, so dev mode injects this instead to stay self-contained. Returns
    a constant reference, honouring the hiker's ``location_name`` for the
    town when given (matching the real resolver's "trust the hiker" rule).
    """
    return PlaceReference(
        town=location_name or "Bad Tölz",
        region="Bavarian Prealps",
        extract="Bad Tölz is a market town in Bavaria on the river Isar.",
        source_url="https://en.wikipedia.org/wiki/Bad_T%C3%B6lz",
        source_title=location_name or "Bad Tölz",
    )


def banner() -> str:
    """Short banner printed when the dev mode is active."""
    return (
        "WEB_FAKE_LLM=1 — using deterministic fake LLM client. "
        "Generated narratives will all be the same fixture text. "
        "Use this only for click-through UI testing."
    )
