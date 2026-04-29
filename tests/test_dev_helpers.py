"""Smoke tests for the dev helpers in ``tests/conftest.py``.

The helpers are imported by ``make test-render``; this guards against
silent regressions in that path.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import render_with_fixtures, sample_narrative

GOLDEN_HTML = Path(__file__).parent / "golden" / "test-render.html"


def test_sample_narrative_has_required_bilingual_fields() -> None:
    n = sample_narrative()
    assert n.title_en and n.title_ru
    assert n.paragraphs_en and n.paragraphs_ru
    assert n.pull_quote_en and n.pull_quote_ru
    assert n.milestone_en and n.milestone_ru
    assert n.selected_photo_indices


def test_render_with_fixtures_writes_html(tmp_path: Path) -> None:
    out = render_with_fixtures(output_dir=tmp_path)
    assert out.is_file()
    assert out.suffix == ".html"
    text = out.read_text(encoding="utf-8")
    assert "Above the fog line" in text
    assert "data:image/jpeg;base64," in text


def test_render_with_fixtures_matches_golden(tmp_path: Path) -> None:
    """Catch any silent change to the HTML template, the elevation SVG,
    photo encoding pipeline, or fixture data. The render is deterministic
    given the same inputs, so byte-equality against
    ``tests/golden/test-render.html`` is the cheapest gate available.

    To update: ``make golden-update``
    """
    out = render_with_fixtures(output_dir=tmp_path)
    actual = out.read_bytes()
    expected = GOLDEN_HTML.read_bytes()
    assert actual == expected, (
        "rendered HTML drifted from tests/golden/test-render.html. "
        "If the change is intentional, regenerate the golden with: "
        "`make golden-update`"
    )
