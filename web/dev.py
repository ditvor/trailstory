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


# ADR-015 chapters shape: six chapter envelopes, each binding one photo
# (photo_index points into the original photo list). Sentence-level
# provenance is preserved inside each chapter's body. Six photo_indices
# in [0..5] match the typical 6+ photo fixture; the pipeline filters
# out-of-range indices defensively so a hike with fewer photos still
# clicks through end-to-end in dev mode.
def _fake_chapter(
    *,
    idx: int,
    chapter_id: str,
    time: str,
    title_en: str,
    title_ru: str,
    title_de: str,
    body_en: str,
    body_ru: str,
    body_de: str,
    photo_index: int,
    source: str = "seed",
) -> dict[str, object]:
    return {
        "id": chapter_id,
        "time": time,
        "place": {
            "en": "Bavarian Alps",
            "ru": "Баварские Альпы",
            "de": "Bayerische Alpen",
        },
        "lat": 47.55 + idx * 0.001,
        "lon": 11.78 + idx * 0.001,
        "title": {"en": title_en, "ru": title_ru, "de": title_de},
        "body": [
            {
                "text": {"en": body_en, "ru": body_ru, "de": body_de},
                "provenance": {"source": source, "reference": "fake-llm dev fixture"},
            }
        ],
        "photo_index": photo_index,
    }


_FAKE_NARRATIVE: Final[str] = json.dumps(
    {
        "schema_version": 4,
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
        "chapters": [
            _fake_chapter(
                idx=0,
                chapter_id="trailhead",
                time="07:00",
                title_en="Trailhead",
                title_ru="Тропа",
                title_de="Wegbeginn",
                body_en=("We left the trailhead at first light, the air sharp with damp moss."),
                body_ru="Вышли на тропу с первыми лучами; воздух пах мхом и хвоей.",  # noqa: RUF001
                body_de="Bei erstem Licht brachen wir auf, die Luft scharf von feuchtem Moos.",
                photo_index=0,
            ),
            _fake_chapter(
                idx=1,
                chapter_id="forest",
                time="08:00",
                title_en="Forest",
                title_ru="Лес",
                title_de="Wald",
                body_en="The pines closed in and the path softened beneath our boots.",
                body_ru="Сосны сомкнулись, и тропа смягчилась под ботинками.",
                body_de="Die Kiefern schlossen sich, der Pfad wurde weich unter den Stiefeln.",
                photo_index=1,
            ),
            _fake_chapter(
                idx=2,
                chapter_id="saddle",
                time="09:30",
                title_en="Saddle",
                title_ru="Седловина",
                title_de="Sattel",
                body_en="By the saddle the cloud was thinning into a soft white scarf.",
                body_ru="К седловине облака уже редели, превращаясь в белый шарф.",  # noqa: RUF001
                body_de="Am Sattel zog die Wolke sich zu einem weichen weißen Schal zusammen.",
                photo_index=2,
            ),
            _fake_chapter(
                idx=3,
                chapter_id="ridge",
                time="11:00",
                title_en="Ridge",
                title_ru="Хребет",
                title_de="Grat",
                body_en="At the ridge the sun broke through and the valley vanished beneath us.",
                body_ru="На хребте солнце пробилось сквозь туман — долина исчезла под нами.",  # noqa: RUF001
                body_de="Am Grat brach die Sonne durch — das Tal verschwand unter uns.",
                photo_index=3,
            ),
            _fake_chapter(
                idx=4,
                chapter_id="rest",
                time="12:00",
                title_en="Rest",
                title_ru="Привал",
                title_de="Rast",
                body_en="We rested on a warm stone, listening to the wind in the pines.",
                body_ru="Мы отдохнули на тёплом камне, слушая ветер в соснах.",
                body_de="Wir rasteten auf einem warmen Stein, lauschten dem Wind in den Kiefern.",
                photo_index=4,
            ),
            _fake_chapter(
                idx=5,
                chapter_id="descent",
                time="13:30",
                title_en="Descent",
                title_ru="Спуск",
                title_de="Abstieg",
                body_en="The descent was kind on tired legs, and the meadow held the last gold.",
                body_ru="Спуск был добрым к уставшим ногам, луг хранил последнее золото.",
                body_de=(
                    "Der Abstieg war freundlich zu müden Beinen, "
                    "und die Wiese hielt das letzte Gold."
                ),
                photo_index=5,
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
