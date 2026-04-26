"""Tests for ``trailstory.cli``.

Per CLAUDE.md the CLI is intentionally thin, so a single end-to-end
happy path against the bundled fixtures (with a mocked LLM client) is
the principal test. Slug / date helpers are unit-tested alongside.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from trailstory.cli import _derive_hike_date, _derive_slug, _slugify, cli
from trailstory.llm.client import AnthropicClient
from trailstory.models import GpxStats, PhotoMeta, Waypoint

FIXTURES = Path(__file__).parent / "fixtures"


def _valid_response_json(n_photos: int = 5) -> str:
    return json.dumps(
        {
            "title_en": "Above the fog line",
            "title_ru": "Над линией тумана",
            "subtitle_en": "A morning above the cloud sea",
            "subtitle_ru": "Утро над морем облаков",
            "paragraphs_en": [
                "We left the trailhead at first light.",
                "By the saddle the cloud was thinning.",
            ],
            "paragraphs_ru": [
                "Вышли на тропу с первыми лучами.",  # noqa: RUF001
                "К седловине облака начали редеть.",  # noqa: RUF001
            ],
            "pull_quote_en": "The fog cleared just as we reached the ridge.",
            "pull_quote_ru": "Туман рассеялся как раз когда мы вышли на хребет.",
            "milestone_en": "First mountain hike",
            "milestone_ru": "Первый горный поход",
            "selected_photo_indices": list(range(n_photos)),
        }
    )


# ── happy path ───────────────────────────────────────────────────────────────


def test_generate_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    fake_client = MagicMock(spec=AnthropicClient)
    fake_client.complete.return_value = _valid_response_json()
    monkeypatch.setattr("trailstory.cli.AnthropicClient", lambda *a, **kw: fake_client)

    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "generate",
            "--photos",
            str(FIXTURES / "sample_photos"),
            "--gpx",
            str(FIXTURES / "sample.gpx"),
            "--seed",
            "The fog cleared just as we reached the ridge.",
            "--name",
            "Mia",
            "--age",
            "5",
            "--out",
            str(out_dir),
            "--location",
            "Bavarian Alps",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "GPX parsed" in result.output
    assert "photos found" in result.output
    assert "Narrative generated" in result.output
    assert "Page rendered" in result.output

    rendered = list(out_dir.glob("*.html"))
    assert len(rendered) == 1
    html = rendered[0].read_text(encoding="utf-8")
    assert "Above the fog line" in html
    assert "Над линией тумана" in html
    assert "data:image/jpeg;base64," in html
    assert "bavarian-alps" in rendered[0].name

    assert fake_client.complete.call_count == 1


def test_generate_surfaces_domain_errors_as_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing GPX path should fail at Click validation (exit 2),
    but a malformed GPX hits our own GpxParseError → exit 1 with red
    line, not a traceback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    bad_gpx = tmp_path / "bad.gpx"
    bad_gpx.write_text("this is not valid xml at all", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "generate",
            "--photos",
            str(FIXTURES / "sample_photos"),
            "--gpx",
            str(bad_gpx),
            "--seed",
            "irrelevant",
            "--out",
            str(tmp_path / "out"),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    assert "error:" in result.output


def test_generate_requires_anthropic_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # avoid picking up a project-local .env

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "generate",
            "--photos",
            str(FIXTURES / "sample_photos"),
            "--gpx",
            str(FIXTURES / "sample.gpx"),
            "--seed",
            "irrelevant",
        ],
    )
    # load_settings() exits 2 with a helpful message
    assert result.exit_code == 2


# ── helpers ──────────────────────────────────────────────────────────────────


def test_slugify_url_safe() -> None:
    assert _slugify("Bavarian Alps") == "bavarian-alps"
    assert _slugify("Zugspitze!") == "zugspitze"
    assert _slugify("  trailing  ") == "trailing"
    assert _slugify("---") == ""
    assert _slugify("Mt. Watzmann (1972 m)") == "mt-watzmann-1972-m"


def test_derive_slug_with_location() -> None:
    assert _derive_slug(date(2025, 8, 15), "Bavarian Alps") == "2025-08-15-bavarian-alps"


def test_derive_slug_without_location() -> None:
    assert _derive_slug(date(2025, 8, 15), None) == "2025-08-15-hike"


def test_derive_slug_with_non_ascii_location_falls_back_to_hike() -> None:
    """Cyrillic / other non-ASCII collapses to empty in _slugify; the slug
    must still be URL-safe."""
    assert _derive_slug(date(2025, 8, 15), "Бавария") == "2025-08-15-hike"


def test_derive_hike_date_prefers_gpx_time() -> None:
    stats = GpxStats(
        distance_km=1.0,
        elevation_gain_m=10,
        duration_min=10,
        start_elev_m=500.0,
        summit_elev_m=510.0,
        waypoints=[
            Waypoint(
                lat=47.5,
                lon=11.7,
                ele_m=500.0,
                time=datetime(2025, 8, 15, 9, 0),
            ),
        ],
    )
    photos = [PhotoMeta(path=Path("/x.jpg"), timestamp=datetime(2025, 9, 1), index=0)]
    assert _derive_hike_date(stats, photos) == date(2025, 8, 15)


def test_derive_hike_date_falls_back_to_photo_timestamp() -> None:
    stats = GpxStats(
        distance_km=1.0,
        elevation_gain_m=10,
        duration_min=10,
        start_elev_m=500.0,
        summit_elev_m=510.0,
        waypoints=[Waypoint(lat=47.5, lon=11.7, ele_m=500.0, time=None)],
    )
    photos = [PhotoMeta(path=Path("/x.jpg"), timestamp=datetime(2025, 9, 1, 14), index=0)]
    assert _derive_hike_date(stats, photos) == date(2025, 9, 1)
