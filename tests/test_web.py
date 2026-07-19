"""Tests for ``web``.

Each test exercises the route handlers via FastAPI's ``TestClient`` and
mocks the Anthropic client at the ``client_factory`` injection point.
The real LLM SDK is never called.

Tests share four things:

* ``_make_client`` — a ``MagicMock`` shaped like ``AnthropicClient`` whose
  ``.complete_stream`` yields a valid narrative JSON in chunks (the SSE
  flow's primary path).
* ``_settings`` — a ``Settings`` instance with a fake API key, so import
  paths that call ``load_settings`` still work even though the factory
  override means the key is never used.
* ``_app_with_storage`` — builds the FastAPI app with a ``Storage``
  rooted under ``tmp_path`` and the sweeper disabled, so workspaces are
  scoped to the test run and we can call ``sweep_expired`` deterministically.
* ``_complete_generation`` — runs the full POST /generate → SSE stream
  → memory ready handshake and returns the slug. Most happy-path tests
  use it instead of asserting the SSE wire format directly.
"""

from __future__ import annotations

import json
import re
import time
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
from trailstory.place import PlaceReference
from web.app import create_app
from web.ratelimit import RateLimiter
from web.routes import (
    MAX_GPX_BYTES,
    MAX_PHOTO_BYTES,
    MAX_PHOTOS_PER_HIKE,
    request_counter_value,
    reset_request_counter,
)
from web.storage import RETENTION_SECONDS, Storage

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_GPX = FIXTURES / "sample.gpx"
SAMPLE_PHOTOS = FIXTURES / "sample_photos"

# The generating page renders the slug as a data attribute on the
# Alpine root; the SSE flow reads it from the URL embedded in
# ``EventSource('/generate/<slug>/stream')``. We pull it from the
# data attribute to keep the assertion stable if the JS shape changes.
_SLUG_RE = re.compile(r'data-slug="([0-9a-f]{12})"')


# ── fixtures ─────────────────────────────────────────────────────────────────


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        anthropic_api_key=SecretStr("sk-test-fake"),
        model="claude-opus-4-7-test",
    )


def _valid_response_json(n_photos: int = 5) -> str:
    """Same shape as ``tests/test_cli.py::_valid_response_json``."""
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
                ru=[
                    "Первые лучи.",
                    "Седловина. Облака редеют.",
                ],
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


def _stream_chunks(payload: str, *, parts: int = 8) -> list[str]:
    """Slice ``payload`` into roughly equal SSE chunks for the fake stream."""
    if not payload:
        return [""]
    step = max(1, len(payload) // parts)
    return [payload[i : i + step] for i in range(0, len(payload), step)]


def _make_client(*, response: str | None = None) -> MagicMock:
    """Mocked WRITER Anthropic client supporting both complete and complete_stream."""
    body = response if response is not None else _valid_response_json()
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-opus-4-7-test"
    fake.complete.return_value = body
    # ``side_effect`` is a callable that returns a fresh iterator on
    # every invocation — important because the SSE retry path may call
    # complete_stream twice.
    fake.complete_stream.side_effect = lambda *_a, **_kw: iter(_stream_chunks(body))
    return fake


def _make_ledger_client() -> MagicMock:
    """Mocked EXTRACTOR Anthropic client (ADR-009).

    Returns a deterministic FactLedger JSON every call. The web pipeline
    runs the extractor synchronously up front before opening the SSE
    response, so the same fixed response works for every test that
    exercises the stream.
    """
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
    """Mocked VISION Anthropic client (ADR-010).

    Returns a deterministic PhotoDescription JSON for every photo. The
    web pipeline runs the describer per-photo before opening the SSE
    response; using a constant keeps test setup small.
    """
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-haiku-4-5-vision-test"
    fake.complete_vision.return_value = json.dumps(
        {
            "people_visible": ["a hiker in a jacket"],
            "objects_visible": ["a forest path"],
            "location_clues": ["evergreen forest"],
            "season_clues": ["overcast light"],
            "body_language_notes": ["walking forward"],
        }
    )
    return fake


def _make_place_client() -> MagicMock:
    """Mocked PLACE-stitch Anthropic client (ADR-017).

    Returns a deterministic tri-lingual place summary. Only invoked when a
    request opts into the place block, so non-place tests never touch it.
    """
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-haiku-4-5-place-test"
    fake.complete.return_value = json.dumps(
        {
            "summary": {
                "en": "Bad Tölz is a market town on the Isar in the Bavarian Prealps.",
                "ru": "Бад-Тёльц — городок на Изаре в Баварских предгорьях.",
                "de": "Bad Tölz ist eine Marktstadt an der Isar in den Bayerischen Voralpen.",
            },
            "used_hiker_details": ["the ridge"],
        }
    )
    return fake


def _stub_place_resolver(
    lat: float, lon: float, location_name: str | None = None
) -> PlaceReference:
    """Offline geocode + Wikipedia stub — no network touched in tests."""
    return PlaceReference(
        town=location_name or "Bad Tölz",
        region="Bavarian Prealps",
        extract="Bad Tölz is a market town in Bavaria on the river Isar.",
        source_url="https://en.wikipedia.org/wiki/Bad_T%C3%B6lz",
        source_title=location_name or "Bad Tölz",
    )


def _app_with_storage(
    storage: Storage,
    *,
    client: MagicMock | None = None,
    ledger_client: MagicMock | None = None,
    vision_client: MagicMock | None = None,
    place_client: MagicMock | None = None,
    rate_limiter: RateLimiter | None = None,
) -> tuple[FastAPI, MagicMock]:
    fake = client if client is not None else _make_client()
    fake_ledger = ledger_client if ledger_client is not None else _make_ledger_client()
    fake_vision = vision_client if vision_client is not None else _make_vision_client()
    fake_place = place_client if place_client is not None else _make_place_client()
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=lambda: fake,
        ledger_client_factory=lambda: fake_ledger,
        vision_client_factory=lambda: fake_vision,
        # ADR-017: inject a fake stitch client + an OFFLINE geocode stub so
        # the place path is fully exercised without any network call.
        place_client_factory=lambda: fake_place,
        place_reference_resolver=_stub_place_resolver,
        rate_limiter=rate_limiter,
        enable_sweeper=False,
    )
    return app, fake


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(root=tmp_path / "trailstory-web", retention_seconds=RETENTION_SECONDS)


@pytest.fixture
def client(storage: Storage) -> Iterator[TestClient]:
    app, _ = _app_with_storage(storage)
    reset_request_counter()
    with TestClient(app) as c:
        yield c


def _read_sample_photos(limit: int = 5) -> list[tuple[str, bytes, str]]:
    """Return a (filename, bytes, content-type) list of fixture photos."""
    out: list[tuple[str, bytes, str]] = []
    for path in sorted(SAMPLE_PHOTOS.iterdir()):
        if path.suffix.lower() in (".jpg", ".jpeg"):
            out.append((path.name, path.read_bytes(), "image/jpeg"))
            if len(out) >= limit:
                break
    return out


def _generate_files(n_photos: int = 5) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Build the multipart files payload used by /generate tests."""
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


def _parse_sse(text: str) -> list[tuple[str, dict[str, object]]]:
    """Parse a captured SSE stream into a list of (event, data) tuples.

    The stream is a sequence of ``event: NAME\\ndata: JSON\\n\\n`` blocks.
    Tests assert against the resulting list rather than walking the
    bytes, which keeps the assertion focused on the contract instead of
    the wire format.
    """
    events: list[tuple[str, dict[str, object]]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name = ""
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        try:
            payload = json.loads(data) if data else {}
        except json.JSONDecodeError:
            payload = {"raw": data}
        events.append((name, payload))
    return events


def _drain_stream(client: TestClient, slug: str) -> list[tuple[str, dict[str, object]]]:
    """GET the SSE endpoint for ``slug`` and return the parsed events."""
    with client.stream("GET", f"/generate/{slug}/stream") as resp:
        assert resp.status_code == 200, resp.read().decode()
        body = b"".join(resp.iter_bytes()).decode("utf-8")
    return _parse_sse(body)


def _complete_generation(
    client: TestClient,
    *,
    description: str = "x",
    style: str = "letter",
    location: str | None = None,
    place: bool = False,
    n_photos: int = 5,
) -> str:
    """Submit the form and drain the SSE stream. Returns the slug."""
    data: dict[str, str] = {"description": description, "style": style}
    if location is not None:
        data["location"] = location
    if place:
        data["place"] = "1"
    response = client.post("/generate", data=data, files=_generate_files(n_photos))
    assert response.status_code == 200, response.text
    slug = _slug_from_generating_page(response.text)
    events = _drain_stream(client, slug)
    # The terminal event must be ``done`` on the happy path.
    names = [e[0] for e in events]
    assert "done" in names, f"stream ended without 'done' event: {names}"
    return slug


# ── pages ────────────────────────────────────────────────────────────────────


def test_landing_page_returns_form(client: TestClient) -> None:
    """The single-page builder collects everything on ``/`` and posts to
    ``/generate``: GPX + photos + description + location + style."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "<form" in body
    assert 'action="/generate"' in body
    assert 'name="gpx"' in body
    assert 'name="photos"' in body
    assert 'name="description"' in body
    assert 'name="location"' in body
    assert 'name="style"' in body
    # The single buildable style id renders as a radio value.
    assert 'value="letter"' in body


def test_landing_page_links_to_privacy_in_new_tab(client: TestClient) -> None:
    """Privacy link should open in a new tab so the upload form is preserved."""
    response = client.get("/")
    body = response.text
    # The "How we handle your photos" link should target _blank.
    assert "How we handle your photos" in body
    # The link to /privacy near the form should have target="_blank".
    assert 'href="/privacy" target="_blank"' in body


def test_landing_page_loads_letter_design_assets(client: TestClient) -> None:
    """Landing extends ``builder_base.html.j2`` and pulls in the Letter CSS.

    Locks the visual refresh in: a regression that drops the design
    system (e.g. someone reverts ``builder_base.html.j2`` to the old
    Tailwind-only shell) would fail this.
    """
    body = client.get("/").text
    assert '<link rel="stylesheet" href="/static/builder.css">' in body
    assert "bp-header" in body
    assert "bp-langnav" in body
    assert "bp-main" in body


def test_landing_page_renders_all_three_languages(client: TestClient) -> None:
    """EN / RU / DE copy is baked into the page — the CSS toggles
    visibility client-side, so all three must be present in the HTML for
    the lang switcher to work without a roundtrip."""
    body = client.get("/").text
    # Hero h1 in each language — these are exact strings, so a typo in
    # any of them surfaces here.
    assert "Keep the day." in body
    assert "Сохрани день." in body
    assert "Bewahre den Tag." in body
    # The to/from docket carries tri-lingual copy too.
    assert "family in Russia, friends elsewhere" in body
    assert "семье в России, друзьям повсюду" in body
    assert "Familie in Russland, Freunde anderswo" in body


def test_landing_page_includes_three_lang_toggle_buttons(client: TestClient) -> None:
    """Header carries an EN / RU / DE toggle wired to ``setLang()``."""
    body = client.get("/").text
    for code, label in (("en", "EN"), ("ru", "RU"), ("de", "DE")):
        assert f"setLang('{code}')" in body
        # Each label appears inside the langnav.
        assert f">{label}</button>" in body


def test_landing_page_honours_lang_query_param(client: TestClient) -> None:
    """``?lang=ru`` paints the page with Russian active on first load."""
    body = client.get("/?lang=ru").text
    assert 'lang="ru"' in body
    assert 'data-lang="ru"' in body


def test_landing_page_falls_back_to_en_on_invalid_lang(client: TestClient) -> None:
    """Crafted ``?lang=xyz`` falls back to English."""
    body = client.get("/?lang=xyz").text
    assert 'lang="en"' in body
    assert 'data-lang="en"' in body


def test_landing_page_renders_all_five_style_cards(client: TestClient) -> None:
    """All five cards from the design proposal render in the picker:
    The Letter / The Zine / Sunday / Postcard Set / Album."""
    body = client.get("/").text
    assert "The Letter" in body
    assert "The Zine" in body
    assert ">Sunday<" in body
    assert "Postcard Set" in body
    assert ">Album<" in body


def test_landing_page_marks_coming_soon_cards(client: TestClient) -> None:
    """The planned cards (Sunday, Postcard, Album) carry the
    ``bp-stylecard--soon`` modifier and a SOON pill — The Letter and
    The Zine are the buildable styles."""
    body = client.get("/").text
    assert "bp-stylecard--soon" in body
    # Each gated card has a disabled radio at its value.
    for value in ("sunday", "postcard", "album"):
        assert f'value="{value}"' in body
    # The SOON pill appears in all three languages.
    assert ">SOON<" in body
    assert ">СКОРО<" in body  # noqa: RUF001
    assert ">BALD<" in body
    # The built cards' radios are not disabled.
    import re

    for value in ("letter", "zine"):
        radio = re.search(
            rf'<input[^>]*name="style"[^>]*value="{value}"[^>]*>',
            body,
        )
        assert radio is not None, value
        assert "disabled" not in radio.group(0), value


def test_generate_rejects_coming_soon_style(client: TestClient) -> None:
    """A crafted POST /generate with a SOON style id is a 400.

    Browsers honour the ``disabled`` attribute on the radio, but an
    HTTP client (or an attacker) can still submit any value — the
    server-side validation (``accepted_style_values()``) rejects
    anything other than the buildable styles.
    """
    for value in ("sunday", "postcard", "album"):
        response = client.post(
            "/generate",
            data={"description": "x", "style": value},
            files=_generate_files(),
        )
        assert response.status_code == 400, value
        assert "Unknown style" in response.json()["detail"]


def test_preview_gpx_returns_parsed_stats(client: TestClient) -> None:
    """``POST /preview/gpx`` parses a GPX file in-memory and returns
    the stats the landing page needs to render the track-loaded card.

    Stateless: nothing should land on disk in the storage root."""
    response = client.post(
        "/preview/gpx",
        files={"gpx": ("track.gpx", SAMPLE_GPX.read_bytes(), "application/gpx+xml")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filename"] == "track.gpx"
    assert body["n_points"] > 0
    assert body["distance_km"] > 0
    assert "elevation_gain_m" in body
    assert "duration_min" in body
    assert "summit_m" in body
    # location_name may be None if the sample GPX has no <name>; the
    # field must still be present so the JS doesn't crash.
    assert "location_name" in body
    # SVG path + endpoints for the mini-route preview.
    assert body["track_d"].startswith("M")
    assert isinstance(body["endpoints"], list)
    assert len(body["endpoints"]) == 2


def test_preview_gpx_rejects_invalid_payload(client: TestClient) -> None:
    response = client.post(
        "/preview/gpx",
        files={"gpx": ("garbage.gpx", b"not actually GPX", "application/gpx+xml")},
    )
    assert response.status_code == 400


def test_preview_gpx_rejects_missing_file(client: TestClient) -> None:
    response = client.post("/preview/gpx")
    assert response.status_code in (400, 422)


def test_preview_gpx_does_not_persist_to_storage(client: TestClient, storage: Storage) -> None:
    """The preview endpoint is read-only. After the call there should be
    no workspace under the storage root — that contract matters because
    rate-limiting and retention sweeps are scoped to created workspaces."""
    before = list(storage.root.iterdir()) if storage.root.is_dir() else []
    client.post(
        "/preview/gpx",
        files={"gpx": ("track.gpx", SAMPLE_GPX.read_bytes(), "application/gpx+xml")},
    )
    after = list(storage.root.iterdir()) if storage.root.is_dir() else []
    assert before == after


def test_preview_photo_returns_exif_date(client: TestClient) -> None:
    """``POST /preview/photo`` reads EXIF DateTimeOriginal from a single
    photo without persisting. The fixture photos have EXIF stamps."""
    name, data, ctype = _read_sample_photos(1)[0]
    response = client.post("/preview/photo", files={"photo": (name, data, ctype)})
    assert response.status_code == 200, response.text
    body = response.json()
    # The fixtures have EXIF dates, so we expect ISO date string.
    assert "hike_date" in body
    if body["hike_date"]:
        # ISO date shape (YYYY-MM-DD).
        assert len(body["hike_date"]) == 10
        assert body["hike_date"][4] == "-"


def test_preview_photo_handles_unreadable_file_gracefully(client: TestClient) -> None:
    """A garbage upload returns ``hike_date=None`` rather than 500.

    The preview is best-effort — the user's actual upload to /generate
    will surface the real error if the photo is truly broken."""
    response = client.post(
        "/preview/photo",
        files={"photo": ("not-a-photo.jpg", b"\x00\x01\x02 not actually a jpeg", "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"hike_date": None}


def test_preview_photo_rejects_missing_file(client: TestClient) -> None:
    response = client.post("/preview/photo")
    assert response.status_code in (400, 422)


def test_landing_page_wires_preview_endpoints(client: TestClient) -> None:
    """The landing template's Alpine root calls ``/preview/gpx`` and
    ``/preview/photo`` on file select. Lock the wire-up so a regression
    that drops the preview JS would fail here."""
    body = client.get("/").text
    assert "/preview/gpx" in body
    assert "/preview/photo" in body
    # The track-loaded card markup must be present (Alpine-toggled
    # visible after the AJAX call returns).
    assert "bp-track-card" in body
    assert "bp-track-replace" in body
    # AUTO-EXTRACTED chip scaffold.
    assert "AUTO-EXTRACTED" in body
    assert "bp-chip" in body


def test_accepted_style_values_only_built() -> None:
    """The :func:`accepted_style_values` helper is the source of truth
    for which style ids the form is allowed to submit. ``letter`` and
    ``zine`` have built renderers that match their design promises;
    the other three cards (Sunday, Postcard, Album) are placeholders
    until their renderers ship."""
    from web.copy import accepted_style_values

    accepted = accepted_style_values()
    assert accepted == frozenset({"letter", "zine"})
    for placeholder in ("sunday", "postcard", "album"):
        assert placeholder not in accepted


def test_built_style_sources_stay_in_sync() -> None:
    """ADR-021 invariant: the three places that know which styles are
    buildable must agree, or a style can be submitted, paid for (LLM
    call), and then fail at render time.

    web.pipeline.Style (the form gate) == accepted_style_values() (the
    picker's non-SOON cards) ⊆ trailstory BUILT_STYLES (the renderer
    gate). When a new style ships, all three move together."""
    from trailstory.models import BUILT_STYLES
    from web.copy import accepted_style_values
    from web.pipeline import Style as WebStyle

    web_values = {s.value for s in WebStyle}
    assert web_values == accepted_style_values()
    assert web_values <= {s.value for s in BUILT_STYLES}


def test_stale_workspace_state_raises_pipeline_error_not_500(tmp_path: Path) -> None:
    """A workspace persisted before an ADR-021-style lineup change (e.g.
    storing ``"style": "editorial"``) must surface as PipelineError — the
    routes map that to a 400 — rather than an unhandled ValueError /
    ValidationError that would 500 the carousel and stream endpoints for
    the whole retention window after a deploy."""
    from web.pipeline import PipelineError, _load_pending_state, _load_state
    from web.storage import Workspace

    ws = Workspace("stale-hike", tmp_path)
    ws.output_dir.mkdir(parents=True)
    ws.state_path.write_text(
        json.dumps({"memory": {"style": "editorial"}, "style": "editorial"}),
        encoding="utf-8",
    )
    ws.pending_state_path.write_text(
        json.dumps({"hike_input": {}, "gpx_stats": {}, "photos": [], "style": "editorial"}),
        encoding="utf-8",
    )
    with pytest.raises(PipelineError, match="older version"):
        _load_state(ws)
    with pytest.raises(PipelineError, match="older version"):
        _load_pending_state(ws)


def test_privacy_page_mentions_retention_and_repo(client: TestClient) -> None:
    response = client.get("/privacy")
    assert response.status_code == 200
    body = response.text
    # Plain-language privacy lifecycle.
    assert "30 minutes" in body
    assert "github.com/ditvor/trailstory" in body
    assert "deleted" in body.lower()


def test_privacy_page_links_to_lifecycle_code_with_line_numbers(
    client: TestClient,
) -> None:
    """The privacy page links to the actual deletion logic on GitHub.

    Locks in the "verify it yourself" promise: the user can click
    through to the line ranges that implement the deletion.
    """
    body = client.get("/privacy").text
    assert "web/storage.py#L" in body
    assert "cleanup_inputs" in body
    assert "sweep_expired" in body


def test_healthz_returns_status_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version_reports_git_sha_from_env(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /version`` echoes ``GIT_SHA`` for deploy traceability."""
    monkeypatch.setenv("GIT_SHA", "abc1234")
    response = client.get("/version")
    assert response.status_code == 200
    body = response.json()
    assert body["git_sha"] == "abc1234"
    assert body["version"] == "0.1.0"


def test_version_falls_back_to_unknown(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Local runs without ``GIT_SHA`` set still return a well-formed payload."""
    monkeypatch.delenv("GIT_SHA", raising=False)
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": "0.1.0", "git_sha": "unknown"}


# ── happy path ───────────────────────────────────────────────────────────────


def test_generate_returns_generating_page(client: TestClient, storage: Storage) -> None:
    """POST /generate returns a 200 generating page, not a redirect.

    The page embeds the slug for the SSE listener to pick up. Pending
    state is on disk so the SSE endpoint can resume — the workspace
    must exist after this call returns.
    """
    response = client.post(
        "/generate",
        data={
            "description": "The fog cleared just as we reached the ridge.",
            "style": "letter",
            "location": "Bavarian Alps",
        },
        files=_generate_files(),
    )
    assert response.status_code == 200
    body = response.text
    slug = _slug_from_generating_page(body)
    assert len(slug) == 12

    # Generating page connects to the SSE endpoint by URL.
    assert f"/generate/{slug}/stream" in body
    # User-facing copy.
    assert "Writing your memory" in body or "writing your memory" in body.lower()

    # Pending state persisted; raw uploads scheduled for cleanup but not
    # yet wiped (BackgroundTask runs after response — we can't assert
    # cleanup directly without flushing it).
    workspace = storage.get_workspace(slug)
    assert workspace is not None
    assert workspace.pending_state_path.is_file()
    # The final state.json does not exist yet — that's the SSE phase.
    assert not workspace.state_path.is_file()


def test_stream_emits_chunks_then_done_then_renders_html(
    client: TestClient, storage: Storage
) -> None:
    """SSE stream pushes chunks, finishes with 'done', the HTML is on disk."""
    response = client.post(
        "/generate",
        data={
            "description": "The fog cleared just as we reached the ridge.",
            "style": "letter",
            "location": "Bavarian Alps",
        },
        files=_generate_files(),
    )
    slug = _slug_from_generating_page(response.text)
    events = _drain_stream(client, slug)
    names = [name for name, _ in events]

    # The wire shape: at least one status, several chunks, a done event.
    assert "status" in names
    assert names.count("chunk") >= 2, names
    assert names[-1] == "done"

    done_payload = events[-1][1]
    assert done_payload.get("redirect") == f"/memory/{slug}"

    # State persisted, pending cleared, HTML rendered.
    workspace = storage.get_workspace(slug)
    assert workspace is not None
    assert workspace.state_path.is_file()
    assert not workspace.pending_state_path.is_file()
    assert (workspace.output_dir / f"{slug}.html").is_file()

    # The follow-up GET serves the rendered HTML.
    page = client.get(f"/memory/{slug}")
    assert page.status_code == 200
    body = page.text
    assert "Above the fog line" in body
    assert "Над линией тумана" in body
    assert "Über der Nebelgrenze" in body
    assert "data:image/jpeg;base64," in body
    # Save for Instagram button is wired into the rendered page.
    assert "Save for Instagram" in body
    assert f'data-slug="{slug}"' in body

    # Counter advanced exactly once on the prep phase.
    assert request_counter_value() == 1


def test_stream_retries_once_on_unparseable_first_attempt(
    storage: Storage,
) -> None:
    """First completed stream returns prose; the second returns valid JSON.

    Asserts: a 'status' event with phase=='regenerating' appears between
    the two attempts, and the final 'done' event still fires.
    """
    fake = _make_client()
    valid = _valid_response_json()
    # First call: prose chunks. Second call: valid JSON chunks.
    call_count = {"n": 0}

    def _stream(*_a: object, **_kw: object) -> Iterator[str]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return iter(_stream_chunks("this is not json at all", parts=4))
        return iter(_stream_chunks(valid))

    fake.complete_stream.side_effect = _stream

    app, _ = _app_with_storage(storage, client=fake)
    with TestClient(app) as c:
        response = c.post(
            "/generate",
            data={"description": "x", "style": "letter"},
            files=_generate_files(),
        )
        slug = _slug_from_generating_page(response.text)
        events = _drain_stream(c, slug)

    phases = [e[1].get("phase") for e in events if e[0] == "status"]
    assert "regenerating" in phases
    assert events[-1][0] == "done"
    assert call_count["n"] == 2


def test_stream_emits_error_on_double_failure(storage: Storage) -> None:
    """Two unparseable attempts in a row land an 'error' event, not 'done'."""
    fake = _make_client()
    fake.complete_stream.side_effect = lambda *_a, **_kw: iter(
        _stream_chunks("still not json", parts=3)
    )

    app, _ = _app_with_storage(storage, client=fake)
    with TestClient(app) as c:
        response = c.post(
            "/generate",
            data={"description": "x", "style": "letter"},
            files=_generate_files(),
        )
        slug = _slug_from_generating_page(response.text)
        events = _drain_stream(c, slug)

    names = [n for n, _ in events]
    assert "done" not in names
    assert names[-1] == "error"


def test_stream_returns_404_after_workspace_consumed(client: TestClient, storage: Storage) -> None:
    """Hitting the SSE endpoint twice cleanly 404s on the second call.

    The first run unlinks ``pending.json`` once rendering succeeds, so a
    refresh of the generating page (or an attacker probing the slug)
    cannot rerun the LLM call — and cannot pay the bill again.
    """
    slug = _complete_generation(client)
    second = client.get(f"/generate/{slug}/stream")
    assert second.status_code == 404


def test_stream_returns_404_for_unknown_slug(client: TestClient) -> None:
    response = client.get("/generate/abcdef012345/stream")
    assert response.status_code == 404


def test_stream_endpoint_sets_event_stream_content_type(
    client: TestClient,
) -> None:
    """SSE clients reject anything that isn't ``text/event-stream``."""
    response = client.post(
        "/generate",
        data={"description": "x", "style": "letter"},
        files=_generate_files(),
    )
    slug = _slug_from_generating_page(response.text)
    with client.stream("GET", f"/generate/{slug}/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers.get("cache-control") == "no-cache"
        # Drain to release the connection.
        b"".join(resp.iter_bytes())


def test_generate_runs_background_cleanup_of_inputs(client: TestClient, storage: Storage) -> None:
    slug = _complete_generation(client)
    workspace = storage.get_workspace(slug)
    assert workspace is not None
    # BackgroundTask wiped raw uploads, but resized + output remain.
    assert not workspace.input_dir.exists()
    assert workspace.resized_dir.is_dir()
    assert any(workspace.resized_dir.iterdir())
    assert workspace.output_dir.is_dir()
    assert (workspace.output_dir / f"{slug}.html").is_file()


def test_carousel_route_renders_slides_after_generate(client: TestClient, storage: Storage) -> None:
    slug = _complete_generation(client)

    car = client.post(f"/memory/{slug}/carousel")
    assert car.status_code == 200
    payload = car.json()
    assert "slides" in payload
    slide_paths = payload["slides"]
    # Title + 5 photos + quote.
    assert len(slide_paths) == 7
    for slide_url in slide_paths:
        assert slide_url.startswith(f"/memory/{slug}/carousel/")

    # Each slide is fetchable and downloadable.
    first = client.get(slide_paths[0])
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/jpeg")


def test_carousel_slide_has_attachment_disposition(
    client: TestClient,
) -> None:
    """Desktop fallback download links rely on Content-Disposition.

    iOS Safari's ``navigator.share({files})`` flow does not need this
    header, but the desktop fallback links served by the rendered
    memory page do — clicking them should land the file in Downloads
    with a meaningful name.
    """
    slug = _complete_generation(client)
    car = client.post(f"/memory/{slug}/carousel").json()
    first = client.get(car["slides"][0])
    disposition = first.headers.get("content-disposition", "")
    assert "attachment" in disposition
    # Slug is in the suggested filename so multiple downloads stay
    # distinct in the user's folder.
    assert slug in disposition


def test_carousel_returns_n_slides_for_n_photos(
    client: TestClient,
) -> None:
    """Title + N photos + quote slides — locks in the carousel shape."""
    fake = _make_client(response=_valid_response_json(n_photos=4))
    app = create_app(
        settings=_settings(),
        storage=Storage(retention_seconds=RETENTION_SECONDS),
        client_factory=lambda: fake,
        ledger_client_factory=lambda: _make_ledger_client(),
        vision_client_factory=lambda: _make_vision_client(),
        enable_sweeper=False,
    )
    with TestClient(app) as c:
        response = c.post(
            "/generate",
            data={"description": "x", "style": "letter"},
            files=_generate_files(n_photos=4),
        )
        slug = _slug_from_generating_page(response.text)
        events = _drain_stream(c, slug)
        assert events[-1][0] == "done"

        car = c.post(f"/memory/{slug}/carousel").json()
        # Title + 4 photos + quote.
        assert len(car["slides"]) == 6


# ── validation ───────────────────────────────────────────────────────────────


def test_generate_rejects_missing_gpx(client: TestClient) -> None:
    files = [(name, payload) for name, payload in _generate_files() if name != "gpx"]
    response = client.post(
        "/generate",
        data={"description": "x", "style": "letter"},
        files=files,
    )
    # FastAPI returns 422 for missing form/file fields by default; our
    # validation is one layer below that and only fires once the upload
    # is present but blank, so 422 is the expected shape here.
    assert response.status_code in (400, 422)


def test_generate_rejects_missing_photos(client: TestClient) -> None:
    files = [
        ("gpx", ("track.gpx", SAMPLE_GPX.read_bytes(), "application/gpx+xml")),
    ]
    response = client.post(
        "/generate",
        data={"description": "x", "style": "letter"},
        files=files,
    )
    assert response.status_code in (400, 422)


def test_generate_rejects_unknown_style(client: TestClient) -> None:
    response = client.post(
        "/generate",
        data={"description": "x", "style": "polaroid"},
        files=_generate_files(),
    )
    assert response.status_code == 400
    assert "Unknown style" in response.json()["detail"]


def test_generate_rejects_unsupported_photo_format(
    client: TestClient,
) -> None:
    files = _generate_files(n_photos=3)
    files.append(("photos", ("note.txt", b"not a photo", "text/plain")))
    response = client.post(
        "/generate",
        data={"description": "x", "style": "letter"},
        files=files,
    )
    assert response.status_code == 400
    assert "Unsupported" in response.json()["detail"]


def test_generate_rejects_oversized_photo(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 50-byte cap forces the smallest fixture photo over the line."""
    app, _ = _app_with_storage(storage)
    monkeypatch.setattr("web.routes.MAX_PHOTO_BYTES", 50)
    with TestClient(app) as c:
        response = c.post(
            "/generate",
            data={"description": "x", "style": "letter"},
            files=_generate_files(n_photos=1),
        )
    assert response.status_code == 413
    assert "Photo exceeds" in response.json()["detail"]


# ── rate limit ───────────────────────────────────────────────────────────────


def test_generate_returns_429_when_over_rate_limit(storage: Storage) -> None:
    """A second call from the same client after hitting the cap gets 429.

    Uses a tiny limit so the rejection path is reachable in two calls.
    The 429 must include a positive ``Retry-After`` header — that is
    the public contract the client UI can rely on.
    """
    app, _ = _app_with_storage(
        storage,
        rate_limiter=RateLimiter(limit=1, window_seconds=3600),
    )
    with TestClient(app) as c:
        first = c.post(
            "/generate",
            data={
                "description": "The fog cleared just as we reached the ridge.",
                "style": "letter",
            },
            files=_generate_files(),
        )
        second = c.post(
            "/generate",
            data={"description": "Same client trying again.", "style": "letter"},
            files=_generate_files(),
        )
    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["retry-after"]) > 0
    assert "Too many memory generations" in second.json()["detail"]


def test_generate_rate_limit_keys_on_fly_client_ip(storage: Storage) -> None:
    """Two distinct ``Fly-Client-IP`` values get independent buckets."""
    app, _ = _app_with_storage(
        storage,
        rate_limiter=RateLimiter(limit=1, window_seconds=3600),
    )
    with TestClient(app) as c:
        first = c.post(
            "/generate",
            data={"description": "Client A.", "style": "letter"},
            files=_generate_files(),
            headers={"Fly-Client-IP": "203.0.113.1"},
        )
        second = c.post(
            "/generate",
            data={"description": "Client B.", "style": "letter"},
            files=_generate_files(),
            headers={"Fly-Client-IP": "203.0.113.2"},
        )
        third = c.post(
            "/generate",
            data={"description": "Client A again.", "style": "letter"},
            files=_generate_files(),
            headers={"Fly-Client-IP": "203.0.113.1"},
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


# ── memory page / carousel 404s ──────────────────────────────────────────────


def test_memory_page_returns_404_for_unknown_slug(client: TestClient) -> None:
    # 12 hex chars but no workspace.
    response = client.get("/memory/abcdef012345")
    assert response.status_code == 404


def test_memory_page_returns_404_for_invalid_slug(client: TestClient) -> None:
    # Path-traversal attempt and bad shapes both 404 cleanly.
    assert client.get("/memory/..%2Fetc").status_code == 404
    assert client.get("/memory/short").status_code == 404


def test_carousel_returns_404_for_unknown_slug(client: TestClient) -> None:
    response = client.post("/memory/abcdef012345/carousel")
    assert response.status_code == 404


def test_carousel_slide_rejects_traversal(client: TestClient, storage: Storage) -> None:
    slug = _complete_generation(client)
    bad = client.get(f"/memory/{slug}/carousel/..%2F..%2Fstate.json")
    assert bad.status_code in (400, 404)


# ── retention sweep ──────────────────────────────────────────────────────────


def test_sweep_expired_deletes_old_workspaces(storage: Storage) -> None:
    """Mtime-based sweep is the privacy backstop after the 30-min window."""
    ws = storage.create_workspace()
    assert ws.root.is_dir()
    # Backdate the workspace past the retention window.
    old = time.time() - (storage.retention_seconds + 60)
    import os

    os.utime(ws.root, (old, old))

    deleted = storage.sweep_expired()
    assert deleted == 1
    assert not ws.root.exists()


def test_sweep_keeps_fresh_workspaces(storage: Storage) -> None:
    ws = storage.create_workspace()
    deleted = storage.sweep_expired()
    assert deleted == 0
    assert ws.root.is_dir()


def test_sweep_clears_workspace_after_retention_window(
    client: TestClient, storage: Storage
) -> None:
    """End-to-end privacy test: upload, generate, age past retention, sweep.

    Confirms ``/privacy``'s "after that window the entire workspace is
    deleted" promise is enforced by code, not just docs.
    """
    slug = _complete_generation(client)
    workspace = storage.get_workspace(slug)
    assert workspace is not None and workspace.root.is_dir()

    # Backdate to simulate the 30-minute retention window having passed.
    old = time.time() - (storage.retention_seconds + 60)
    import os

    os.utime(workspace.root, (old, old))

    deleted = storage.sweep_expired()
    assert deleted >= 1
    assert not workspace.root.exists()
    # Memory page now 404s — the page is gone.
    assert client.get(f"/memory/{slug}").status_code == 404


# ── upload limit constants exist and are sane ───────────────────────────────


def test_upload_limit_constants_are_sane() -> None:
    """Lock the user-visible numbers in. A regression here would change
    the contract documented on the privacy page and in the form copy."""
    assert MAX_GPX_BYTES >= 1 * 1024 * 1024
    assert MAX_PHOTO_BYTES >= 1 * 1024 * 1024
    assert MAX_PHOTOS_PER_HIKE >= 6  # narrative needs 6-8 indices


# ── lifespan + sweeper integration ───────────────────────────────────────────


def test_create_app_starts_and_stops_sweeper_cleanly(storage: Storage) -> None:
    """With sweeper enabled, the lifespan hooks must not raise on enter/exit.

    We don't wait for an actual sweep tick — that's a 5-minute cadence —
    just that the task is created and cancelled cleanly when the app
    shuts down.
    """
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=lambda: _make_client(),
        enable_sweeper=True,
    )
    with TestClient(app) as c:
        response = c.get("/healthz")
        assert response.status_code == 200


# ── dev / fake-LLM mode ──────────────────────────────────────────────────────


def test_fake_client_factory_drives_full_pipeline(storage: Storage) -> None:
    """The dev-mode fake client must satisfy the same contract the real
    one does — return parseable narrative JSON whose
    ``selected_photo_indices`` survive the pipeline's index-bounds filter
    and produce a renderable Memory.

    Locks the dev-mode shortcut against accidental drift in
    ``NarrativeOutput`` (a new required field would break the fake JSON
    silently otherwise).
    """
    from web.dev import (
        make_fake_client_factory,
        make_fake_ledger_client_factory,
        make_fake_vision_client_factory,
    )

    factory = make_fake_client_factory()
    ledger_factory = make_fake_ledger_client_factory()
    vision_factory = make_fake_vision_client_factory()
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=factory,
        ledger_client_factory=ledger_factory,
        vision_client_factory=vision_factory,
        enable_sweeper=False,
    )
    with TestClient(app) as c:
        response = c.post(
            "/generate",
            data={"description": "x", "style": "letter"},
            files=_generate_files(),
        )
    assert response.status_code == 200
    slug = _slug_from_generating_page(response.text)
    workspace = storage.get_workspace(slug)
    assert workspace is not None
    assert workspace.pending_state_path.is_file()

    # Drain the SSE stream — the fake stream factory must yield real
    # chunks that round-trip through generate_narrative_stream cleanly.
    with TestClient(app) as c:
        events = _drain_stream(c, slug)
    assert events[-1][0] == "done"
    assert (workspace.output_dir / f"{slug}.html").is_file()


# ── ADR-017: "about this place" web wiring ──────────────────────────────────


def test_landing_form_has_place_checkbox(client: TestClient) -> None:
    """The builder form exposes the opt-in place toggle (off by default)."""
    body = client.get("/").text
    assert 'name="place"' in body
    assert 'value="1"' in body
    # Tri-lingual label is baked in for the client-side language switch.
    assert "about this place" in body
    assert "о месте" in body  # noqa: RUF001


def test_generate_with_place_renders_block(client: TestClient) -> None:
    """place=1 flows through prep → stream → render: the block is in the HTML."""
    slug = _complete_generation(client, location="Bad Tölz", place=True)
    html = client.get(f"/memory/{slug}").text
    assert 'class="place"' in html
    assert "Bad Tölz" in html
    # The stitch summary (from the fake place client) is present.
    assert "market town on the Isar" in html
    # CC BY-SA source attribution link.
    assert "en.wikipedia.org/wiki/Bad_T" in html


def test_generate_without_place_omits_block(client: TestClient) -> None:
    """Default generation (no place toggle) carries no place markup."""
    slug = _complete_generation(client, location="Bad Tölz", place=False)
    html = client.get(f"/memory/{slug}").text
    assert 'class="place"' not in html
    assert "market town on the Isar" not in html
