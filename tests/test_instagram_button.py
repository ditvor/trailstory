"""Tests for the "Save for Instagram" button on the rendered memory page.

The button is part of every style template (editorial / log /
encyclopedia). On click, the page POSTs to ``/memory/{slug}/carousel``,
fetches each slide URL as a Blob, and either calls ``navigator.share``
with the files (iOS Safari path) or renders fallback download links
(desktop). The Python-side contract these tests exercise:

* The button renders into all three style templates.
* The button carries the slug as ``data-slug`` so the JS can build the
  POST URL without templating it inline.
* ``POST /memory/{slug}/carousel`` returns exactly N+2 slides for N
  selected photos (title + N + closing quote).
* Each slide is fetchable, has a JPEG content type, and a download-
  friendly Content-Disposition header for the desktop fallback.

The tests mock the LLM to avoid paying for narrative calls.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.conftest import paragraphs_dict_from_strings
from trailstory.config import Settings
from trailstory.llm.client import AnthropicClient
from web.app import create_app
from web.storage import RETENTION_SECONDS, Storage

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_GPX = FIXTURES / "sample.gpx"
SAMPLE_PHOTOS = FIXTURES / "sample_photos"

_SLUG_RE = re.compile(r'data-slug="([0-9a-f]{12})"')


# ── fixtures ─────────────────────────────────────────────────────────────────


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        anthropic_api_key=SecretStr("sk-test-fake"),
        model="claude-opus-4-7-test",
    )


def _valid_response_json(n_photos: int = 5) -> str:
    return json.dumps(
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
            "paragraphs": paragraphs_dict_from_strings(
                en=["First light.", "Saddle. Cloud thinning."],
                ru=["Первые лучи.", "Седловина. Облака редеют."],
                de=["Erstes Licht.", "Sattel. Wolke lichtet sich."],
            ),
            "pull_quote": {
                "en": "The fog cleared just as we reached the ridge.",
                "ru": "Туман рассеялся как раз когда мы вышли на хребет.",
                "de": "Der Nebel lichtete sich, gerade als wir den Grat erreichten.",
            },
            "milestone": {
                "en": "First mountain hike",
                "ru": "Первый горный поход",
                "de": "Erste Bergwanderung",
            },
            "selected_photo_indices": list(range(n_photos)),
        }
    )


def _stream_chunks(payload: str, *, parts: int = 6) -> list[str]:
    if not payload:
        return [""]
    step = max(1, len(payload) // parts)
    return [payload[i : i + step] for i in range(0, len(payload), step)]


def _make_client(*, response: str | None = None) -> MagicMock:
    body = response if response is not None else _valid_response_json()
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-opus-4-7-test"
    fake.complete.return_value = body
    fake.complete_stream.side_effect = lambda *_a, **_kw: iter(_stream_chunks(body))
    return fake


def _make_ledger_client() -> MagicMock:
    """Mocked ADR-009 extractor client. Constant ledger JSON for every call."""
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-haiku-4-5-test"
    fake.complete.return_value = json.dumps(
        {
            "people": [{"name": "Mia", "role": "baby in carrier"}],
            "weather": "amazing weather",
            "chronology": [
                {
                    "time_of_day": "morning",
                    "activity": "ascent through fog",
                    "emotion": "anticipation",
                    "objects_mentioned": ["fog", "ridge"],
                },
            ],
        }
    )
    return fake


def _make_vision_client() -> MagicMock:
    """Mocked ADR-010 vision client. Constant PhotoDescription JSON per photo."""
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-haiku-4-5-vision-test"
    fake.complete_vision.return_value = json.dumps(
        {
            "people_visible": [],
            "objects_visible": ["path"],
            "location_clues": [],
            "season_clues": [],
            "body_language_notes": [],
        }
    )
    return fake


def _read_sample_photos(limit: int) -> list[tuple[str, bytes, str]]:
    out: list[tuple[str, bytes, str]] = []
    for path in sorted(SAMPLE_PHOTOS.iterdir()):
        if path.suffix.lower() in (".jpg", ".jpeg"):
            out.append((path.name, path.read_bytes(), "image/jpeg"))
            if len(out) >= limit:
                break
    return out


def _generate_files(n_photos: int) -> list[tuple[str, tuple[str, bytes, str]]]:
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("gpx", ("track.gpx", SAMPLE_GPX.read_bytes(), "application/gpx+xml")),
    ]
    for name, data, ctype in _read_sample_photos(n_photos):
        files.append(("photos", (name, data, ctype)))
    return files


def _slug_from_generating_page(html: str) -> str:
    match = _SLUG_RE.search(html)
    assert match is not None, "generating page is missing the data-slug attribute"
    return match.group(1)


def _drain_stream(client: TestClient, slug: str) -> list[str]:
    """Drain the SSE stream and return the list of event names."""
    with client.stream("GET", f"/generate/{slug}/stream") as resp:
        assert resp.status_code == 200
        text = b"".join(resp.iter_bytes()).decode("utf-8")
    names: list[str] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("event:"):
                names.append(line[len("event:") :].strip())
    return names


@pytest.fixture
def app_factory(
    tmp_path: Path,
) -> Iterator[FastAPI]:
    storage = Storage(root=tmp_path / "trailstory-web", retention_seconds=RETENTION_SECONDS)
    fake = _make_client()
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=lambda: fake,
        ledger_client_factory=lambda: _make_ledger_client(),
        vision_client_factory=lambda: _make_vision_client(),
        enable_sweeper=False,
    )
    yield app


@pytest.fixture
def app_factory_4_photos(tmp_path: Path) -> Iterator[FastAPI]:
    storage = Storage(root=tmp_path / "trailstory-web", retention_seconds=RETENTION_SECONDS)
    fake = _make_client(response=_valid_response_json(n_photos=4))
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=lambda: fake,
        ledger_client_factory=lambda: _make_ledger_client(),
        vision_client_factory=lambda: _make_vision_client(),
        enable_sweeper=False,
    )
    yield app


def _generate_and_render(
    app: FastAPI, n_photos: int = 5, style: str = "editorial"
) -> tuple[TestClient, str]:
    """Run the prep + SSE + render flow, return the (client, slug)."""
    client = TestClient(app)
    response = client.post(
        "/generate",
        data={"description": "x", "style": style},
        files=_generate_files(n_photos),
    )
    assert response.status_code == 200, response.text
    slug = _slug_from_generating_page(response.text)
    names = _drain_stream(client, slug)
    assert names[-1] == "done", names
    return client, slug


# ── button rendering ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("style", ["editorial", "log", "encyclopedia"])
def test_save_for_instagram_button_renders_in_every_style(tmp_path: Path, style: str) -> None:
    """All three style templates ship the button so the user gets it
    whichever visual treatment they pick."""
    storage = Storage(root=tmp_path / "trailstory-web", retention_seconds=RETENTION_SECONDS)
    fake = _make_client()
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=lambda: fake,
        ledger_client_factory=lambda: _make_ledger_client(),
        vision_client_factory=lambda: _make_vision_client(),
        enable_sweeper=False,
    )
    client, slug = _generate_and_render(app, style=style)
    page = client.get(f"/memory/{slug}").text

    assert "Save for Instagram" in page
    assert f'data-slug="{slug}"' in page
    # The carousel POST URL is built client-side from data-slug + a
    # constant prefix. Lock the prefix so a refactor that breaks the
    # button doesn't pass tests silently.
    assert "/memory/" in page and "/carousel" in page


def test_button_targets_navigator_share_with_files(app_factory: FastAPI) -> None:
    """The two-tap iOS flow relies on ``navigator.canShare({files})``;
    the desktop fallback uses download links. Both code paths must be
    present in the rendered page."""
    client, slug = _generate_and_render(app_factory)
    page = client.get(f"/memory/{slug}").text

    # iOS Safari path.
    assert "navigator.canShare" in page
    assert "navigator.share" in page
    # Desktop fallback.
    assert "showFallbackLinks" in page
    # Each download link sets the ``download`` attribute so clicks save
    # rather than navigate.
    assert "setAttribute('download'" in page


# ── carousel POST shape ──────────────────────────────────────────────────────


def test_carousel_post_returns_n_plus_two_slides(app_factory: FastAPI) -> None:
    """For 5 selected photos the carousel is title + 5 + quote = 7 slides."""
    client, slug = _generate_and_render(app_factory, n_photos=5)

    res = client.post(f"/memory/{slug}/carousel")
    assert res.status_code == 200
    payload = res.json()
    assert "slides" in payload
    slides = payload["slides"]
    assert len(slides) == 7
    # Slug-namespaced URLs only.
    for url in slides:
        assert url.startswith(f"/memory/{slug}/carousel/")


def test_carousel_post_returns_six_slides_for_four_photos(
    app_factory_4_photos: FastAPI,
) -> None:
    """Smaller hike, smaller carousel — the count tracks selected photos."""
    client, slug = _generate_and_render(app_factory_4_photos, n_photos=4)

    res = client.post(f"/memory/{slug}/carousel")
    assert res.status_code == 200
    slides = res.json()["slides"]
    assert len(slides) == 6  # title + 4 + quote


def test_carousel_slide_has_jpeg_content_type_and_attachment_disposition(
    app_factory: FastAPI,
) -> None:
    """Every slide must serve as a JPEG with a download-friendly
    Content-Disposition so the desktop fallback link saves with a
    meaningful filename instead of opening inline."""
    client, slug = _generate_and_render(app_factory)
    slides = client.post(f"/memory/{slug}/carousel").json()["slides"]

    for slide_url in slides:
        res = client.get(slide_url)
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/jpeg")
        disposition = res.headers.get("content-disposition", "")
        assert "attachment" in disposition
        assert slug in disposition  # slug-namespaced filename


def test_carousel_post_idempotent(app_factory: FastAPI) -> None:
    """Two POSTs in a row produce the same slide count — the renderer
    overwrites in place rather than appending or 4xxing."""
    client, slug = _generate_and_render(app_factory)

    first = client.post(f"/memory/{slug}/carousel").json()
    second = client.post(f"/memory/{slug}/carousel").json()
    assert first == second
