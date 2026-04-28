"""Tests for ``trailstory.config``.

Covers env-var → ``Settings`` field round-trips for each output-preference
field added in ``chore/settings-expansion`` and an integration test that
threads ``PHOTO_MAX_EDGE`` through ``load_photos`` to verify the resize
behaviour responds to the configured value.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from trailstory.config import Settings, load_settings
from trailstory.photos import load_photos


@pytest.fixture(autouse=True)
def _isolated_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop ``Settings`` from reading the project-local ``.env`` file.

    ``BaseSettings`` resolves ``env_file=".env"`` relative to the current
    working directory. Without this fixture, tests that set env-vars via
    ``monkeypatch`` would still inherit values from the developer's real
    ``.env`` and the assertions below would be non-hermetic.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")


# ── defaults ─────────────────────────────────────────────────────────────────


def test_defaults_match_documented_values() -> None:
    settings = load_settings()

    assert settings.photo_max_edge == 1800
    assert settings.photo_quality == 90
    assert settings.instagram_quality == 90
    assert settings.narrative_max_tokens == 4096
    assert settings.narrative_max_retries == 3


# ── env-var round-trips ──────────────────────────────────────────────────────


def test_photo_max_edge_round_trips_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOTO_MAX_EDGE", "1234")
    assert Settings().photo_max_edge == 1234  # type: ignore[call-arg]


def test_photo_quality_round_trips_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOTO_QUALITY", "75")
    assert Settings().photo_quality == 75  # type: ignore[call-arg]


def test_instagram_quality_round_trips_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTAGRAM_QUALITY", "82")
    assert Settings().instagram_quality == 82  # type: ignore[call-arg]


def test_narrative_max_tokens_round_trips_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATIVE_MAX_TOKENS", "8192")
    assert Settings().narrative_max_tokens == 8192  # type: ignore[call-arg]


def test_narrative_max_retries_round_trips_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NARRATIVE_MAX_RETRIES", "7")
    assert Settings().narrative_max_retries == 7  # type: ignore[call-arg]


# ── integration ──────────────────────────────────────────────────────────────


def test_photo_max_edge_env_var_actually_resizes_photos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: setting ``PHOTO_MAX_EDGE=400`` must produce JPEGs whose
    longest edge is exactly 400px after ``load_photos`` runs.

    This is the contract that justifies having a setting at all — if the
    plumbing breaks, photo size silently reverts to the module-level default.
    """
    monkeypatch.setenv("PHOTO_MAX_EDGE", "400")

    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (4000, 1500), (120, 130, 140)).save(
        src / "wide.jpg", format="JPEG", quality=85
    )
    Image.new("RGB", (1500, 4000), (120, 130, 140)).save(
        src / "tall.jpg", format="JPEG", quality=85
    )

    settings = load_settings()
    assert settings.photo_max_edge == 400  # sanity-check the env var landed

    photos = load_photos(
        src,
        tmp_path / "out",
        max_edge=settings.photo_max_edge,
        quality=settings.photo_quality,
    )

    sizes = {p.path.stem: Image.open(p.path).size for p in photos}
    assert max(sizes["wide"]) == 400
    assert max(sizes["tall"]) == 400
