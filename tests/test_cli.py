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

from tests.conftest import paragraphs_dict_from_strings
from trailstory.cli import _derive_hike_date, _derive_slug, _slugify, cli
from trailstory.llm.client import AnthropicClient
from trailstory.models import GpxStats, PhotoMeta, Waypoint

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_narrative_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the narrative cache at a per-test temp directory.

    Without this every CLI test would share ``~/.cache/trailstory`` and
    a previous run's cached entry would silently turn the next run's
    "first call" into a cache hit, breaking ``call_count`` assertions.
    """
    monkeypatch.setenv("TRAILSTORY_CACHE_DIR", str(tmp_path / "narrative-cache"))


_FAKE_LEDGER_JSON = json.dumps(
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


_FAKE_PHOTO_DESCRIPTION_JSON = json.dumps(
    {
        "people_visible": ["a hiker in a jacket"],
        "objects_visible": ["a forest path"],
        "location_clues": ["evergreen forest"],
        "season_clues": ["overcast light"],
        "body_language_notes": ["walking forward"],
    }
)


def _make_fake_client() -> MagicMock:
    """Mocked Anthropic client that serves all three ADR-009/010 passes.

    The CLI constructs three ``AnthropicClient`` instances per generate run
    (writer + ledger extractor + vision describer). All come through the
    patched ``trailstory.cli.AnthropicClient`` constructor, which in these
    tests returns this single fake. The fake therefore dispatches each
    ``complete`` call to the right canned response based on the system
    prompt — the writer's persona vs the ledger extractor's — and serves
    a canned PhotoDescription for every ``complete_vision`` call.

    ``.model`` is a real string because the cache key uses it as a dict
    value; ``MagicMock(spec=...)`` would expose ``.model`` as a MagicMock
    that ``json.dumps`` chokes on.
    """
    fake = MagicMock(spec=AnthropicClient)
    fake.model = "claude-opus-4-7-test"

    # Default writer response set by individual tests via
    # fake.complete.return_value = _valid_response_json(...).
    # We wrap that with a side_effect that intercepts the ledger pass
    # and serves the canned extractor JSON instead.
    def _dispatch(*, prompt: str, system: str) -> str:
        if "ledger" in system.lower() or "fact ledger" in system.lower():
            return _FAKE_LEDGER_JSON
        # Writer pass — return whatever return_value the test set.
        result: object = fake.complete.return_value
        return str(result) if not isinstance(result, str) else result

    fake.complete.side_effect = _dispatch
    # Phase 3: the vision pass uses complete_vision(); return a constant
    # PhotoDescription so describe_photos succeeds in CLI tests.
    fake.complete_vision.return_value = _FAKE_PHOTO_DESCRIPTION_JSON
    return fake


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
                en=[
                    "We left the trailhead at first light.",
                    "By the saddle the cloud was thinning.",
                ],
                ru=[
                    "Вышли на тропу с первыми лучами.",  # noqa: RUF001
                    "К седловине облака начали редеть.",  # noqa: RUF001
                ],
                de=[
                    "Bei erstem Licht brachen wir auf.",
                    "Am Sattel begann die Wolke sich zu lichten.",
                ],
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


# ── happy path ───────────────────────────────────────────────────────────────


def test_generate_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    fake_client = _make_fake_client()
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
    assert "Über der Nebelgrenze" in html
    assert "data:image/jpeg;base64," in html
    assert "bavarian-alps" in rendered[0].name

    # Under ADR-009 a generate run makes two complete() calls per pipeline:
    # one for the ledger extractor, one for the writer. Same fake serves both.
    assert fake_client.complete.call_count == 2


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


def test_generate_with_instagram_flag_writes_carousel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    fake_client = _make_fake_client()
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
            "irrelevant",
            "--out",
            str(out_dir),
            "--location",
            "Bavarian Alps",
            "--instagram",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Carousel rendered" in result.output

    carousel_dirs = list(out_dir.glob("*/carousel"))
    assert len(carousel_dirs) == 1
    slides = sorted(carousel_dirs[0].glob("*.jpg"))
    # 1 title + 5 fixture photos + 1 quote
    assert len(slides) == 7
    assert slides[0].name == "00_title.jpg"
    assert slides[-1].name.endswith("_quote.jpg")


def test_generate_second_run_uses_cache_and_skips_llm_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the cache: a repeat run with identical inputs
    must serve the prior narrative from disk and not call the LLM again."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    fake_client = _make_fake_client()
    fake_client.complete.return_value = _valid_response_json()
    monkeypatch.setattr("trailstory.cli.AnthropicClient", lambda *a, **kw: fake_client)

    out_dir = tmp_path / "out"
    args = [
        "generate",
        "--photos",
        str(FIXTURES / "sample_photos"),
        "--gpx",
        str(FIXTURES / "sample.gpx"),
        "--seed",
        "The fog cleared just as we reached the ridge.",
        "--out",
        str(out_dir),
        "--location",
        "Bavarian Alps",
    ]
    runner = CliRunner()
    first = runner.invoke(cli, args, catch_exceptions=False)
    second = runner.invoke(cli, args, catch_exceptions=False)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    # ADR-009 two-pass: first run does ledger + writer = 2 calls. Second run
    # hits the narrative cache and skips BOTH passes (cache stores the
    # final NarrativeOutput, so the ledger pass is short-circuited too).
    assert fake_client.complete.call_count == 2
    # The HTML must still be produced both times (rendering is not cached).
    assert list(out_dir.glob("*.html"))


def test_generate_no_cache_flag_forces_fresh_llm_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-cache`` must force a fresh LLM call even when a cached
    entry would otherwise satisfy the request."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")

    fake_client = _make_fake_client()
    fake_client.complete.return_value = _valid_response_json()
    monkeypatch.setattr("trailstory.cli.AnthropicClient", lambda *a, **kw: fake_client)

    out_dir = tmp_path / "out"
    args = [
        "generate",
        "--photos",
        str(FIXTURES / "sample_photos"),
        "--gpx",
        str(FIXTURES / "sample.gpx"),
        "--seed",
        "The fog cleared just as we reached the ridge.",
        "--out",
        str(out_dir),
        "--location",
        "Bavarian Alps",
    ]
    runner = CliRunner()
    runner.invoke(cli, args, catch_exceptions=False)
    # Same inputs but with --no-cache: must bypass the cache entirely.
    runner.invoke(cli, [*args, "--no-cache"], catch_exceptions=False)

    # ADR-009: each generate run is 2 calls (ledger + writer). First run
    # caches, second --no-cache run forces a fresh pipeline = 4 total.
    assert fake_client.complete.call_count == 4


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


def test_generate_passes_model_env_override_into_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``MODEL=claude-sonnet-4-6`` must flow through ``Settings`` into the
    WRITER ``AnthropicClient`` constructor — that is the only knob the user
    has to swap writers without editing ``config.py``. Under ADR-009 the CLI
    also constructs a second (ledger) client from ``LEDGER_MODEL``; this
    test only asserts on the writer side, but captures every constructor
    call so the assertion can pick out the writer one specifically.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    monkeypatch.setenv("MODEL", "claude-sonnet-4-6")

    fake_client = _make_fake_client()
    fake_client.complete.return_value = _valid_response_json()

    captured_models: list[str] = []

    def _capture(*_args: object, **kwargs: object) -> MagicMock:
        captured_models.append(str(kwargs.get("model")))
        return fake_client

    monkeypatch.setattr("trailstory.cli.AnthropicClient", _capture)

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
            "--out",
            str(tmp_path / "out"),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    # Writer client built first, ledger client second (ADR-009), vision
    # client third (ADR-010).
    assert "claude-sonnet-4-6" in captured_models, captured_models
    # All three passes happened — sanity check the multi-pass shape
    # didn't silently collapse.
    assert len(captured_models) == 3, captured_models


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
