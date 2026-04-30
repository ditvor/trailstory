"""Tests for ``web``.

Each test exercises the route handlers via FastAPI's ``TestClient`` and
mocks the Anthropic client at the ``client_factory`` injection point.
The real LLM SDK is never called.

Tests share three things:

* ``_make_client`` — a ``MagicMock`` shaped like ``AnthropicClient`` whose
  ``.complete`` returns a valid narrative JSON.
* ``_settings`` — a ``Settings`` instance with a fake API key, so import
  paths that call ``load_settings`` still work even though the factory
  override means the key is never used.
* ``_app_with_storage`` — builds the FastAPI app with a ``Storage``
  rooted under ``tmp_path`` and the sweeper disabled, so workspaces are
  scoped to the test run and we can call ``sweep_expired`` deterministically.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from trailstory.config import Settings
from trailstory.llm.client import AnthropicClient
from web.app import create_app
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
                "en": ["First light.", "Saddle. Cloud thinning."],
                "ru": [
                    "Первые лучи.",
                    "Седловина. Облака редеют.",
                ],
                "de": ["Erstes Licht.", "Sattel. Wolke lichtet sich."],
            },
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


def _make_client() -> MagicMock:
    """Mocked Anthropic client whose ``.model`` is a real string."""
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-opus-4-7-test"
    fake.complete.return_value = _valid_response_json()
    return fake


def _app_with_storage(
    storage: Storage,
    *,
    client: MagicMock | None = None,
) -> tuple[FastAPI, MagicMock]:
    fake = client if client is not None else _make_client()
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=lambda: fake,
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


# ── pages ────────────────────────────────────────────────────────────────────


def test_landing_page_returns_form(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "<form" in body
    assert 'name="gpx"' in body
    assert 'name="photos"' in body
    assert 'name="description"' in body
    # All three styles render as radio options.
    assert 'value="editorial"' in body
    assert 'value="log"' in body
    assert 'value="encyclopedia"' in body


def test_privacy_page_mentions_retention_and_repo(client: TestClient) -> None:
    response = client.get("/privacy")
    assert response.status_code == 200
    body = response.text
    # Plain-language privacy lifecycle.
    assert "30 minutes" in body
    assert "github.com/ditvor/trailstory" in body
    assert "deleted" in body.lower()


def test_healthz_returns_status_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── happy path ───────────────────────────────────────────────────────────────


def test_generate_renders_memory_and_redirects(client: TestClient, storage: Storage) -> None:
    response = client.post(
        "/generate",
        data={
            "description": "The fog cleared just as we reached the ridge.",
            "style": "editorial",
            "location": "Bavarian Alps",
        },
        files=_generate_files(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    redirect = response.headers["location"]
    assert redirect.startswith("/memory/")
    slug = redirect.rsplit("/", 1)[-1]
    assert len(slug) == 12

    # The follow-up GET serves the rendered HTML.
    page = client.get(redirect)
    assert page.status_code == 200
    body = page.text
    assert "Above the fog line" in body
    assert "Над линией тумана" in body
    assert "Über der Nebelgrenze" in body
    assert "data:image/jpeg;base64," in body

    # Counter advanced exactly once.
    assert request_counter_value() == 1

    # State persisted for carousel use.
    workspace = storage.get_workspace(slug)
    assert workspace is not None
    assert workspace.state_path.is_file()


def test_generate_runs_background_cleanup_of_inputs(client: TestClient, storage: Storage) -> None:
    response = client.post(
        "/generate",
        data={"description": "x", "style": "editorial"},
        files=_generate_files(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    slug = response.headers["location"].rsplit("/", 1)[-1]
    workspace = storage.get_workspace(slug)
    assert workspace is not None
    # BackgroundTask wiped raw uploads, but resized + output remain.
    assert not workspace.input_dir.exists()
    assert workspace.resized_dir.is_dir()
    assert any(workspace.resized_dir.iterdir())
    assert workspace.output_dir.is_dir()
    assert (workspace.output_dir / f"{slug}.html").is_file()


def test_carousel_route_renders_slides_after_generate(client: TestClient, storage: Storage) -> None:
    response = client.post(
        "/generate",
        data={"description": "x", "style": "editorial"},
        files=_generate_files(),
        follow_redirects=False,
    )
    slug = response.headers["location"].rsplit("/", 1)[-1]

    car = client.post(f"/memory/{slug}/carousel")
    assert car.status_code == 200
    payload = car.json()
    assert "slides" in payload
    slide_paths = payload["slides"]
    # Title + 5 photos + quote.
    assert len(slide_paths) == 7
    for slide_url in slide_paths:
        assert slide_url.startswith(f"/memory/{slug}/carousel/")

    # Each slide is fetchable.
    first = client.get(slide_paths[0])
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/jpeg")


# ── validation ───────────────────────────────────────────────────────────────


def test_generate_rejects_missing_gpx(client: TestClient) -> None:
    files = [(name, payload) for name, payload in _generate_files() if name != "gpx"]
    response = client.post(
        "/generate",
        data={"description": "x", "style": "editorial"},
        files=files,
        follow_redirects=False,
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
        data={"description": "x", "style": "editorial"},
        files=files,
        follow_redirects=False,
    )
    assert response.status_code in (400, 422)


def test_generate_rejects_unknown_style(client: TestClient) -> None:
    response = client.post(
        "/generate",
        data={"description": "x", "style": "polaroid"},
        files=_generate_files(),
        follow_redirects=False,
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
        data={"description": "x", "style": "editorial"},
        files=files,
        follow_redirects=False,
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
            data={"description": "x", "style": "editorial"},
            files=_generate_files(n_photos=1),
            follow_redirects=False,
        )
    assert response.status_code == 413
    assert "Photo exceeds" in response.json()["detail"]


def test_generate_surfaces_pipeline_error_as_400(
    storage: Storage,
) -> None:
    fake = _make_client()
    fake.complete.return_value = "this is not json"
    app, _ = _app_with_storage(storage, client=fake)
    with TestClient(app) as c:
        response = c.post(
            "/generate",
            data={"description": "x", "style": "editorial"},
            files=_generate_files(),
            follow_redirects=False,
        )
    assert response.status_code == 400
    # Workspace was deleted to keep tmp clean after the failure.
    assert not list(storage.root.iterdir())


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
    # First, generate a real workspace so the slug is valid.
    gen = client.post(
        "/generate",
        data={"description": "x", "style": "editorial"},
        files=_generate_files(),
        follow_redirects=False,
    )
    slug = gen.headers["location"].rsplit("/", 1)[-1]
    # Then try to read outside carousel/.
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
    from web.dev import make_fake_client_factory

    factory = make_fake_client_factory()
    app = create_app(
        settings=_settings(),
        storage=storage,
        client_factory=factory,
        enable_sweeper=False,
    )
    with TestClient(app) as c:
        response = c.post(
            "/generate",
            data={"description": "x", "style": "editorial"},
            files=_generate_files(),
            follow_redirects=False,
        )
    assert response.status_code == 303
    slug = response.headers["location"].rsplit("/", 1)[-1]
    workspace = storage.get_workspace(slug)
    assert workspace is not None
    assert (workspace.output_dir / f"{slug}.html").is_file()
