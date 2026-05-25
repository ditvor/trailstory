"""Smoke tests for the dev helpers in ``tests/conftest.py``.

The helpers are imported by ``make test-render``; this guards against
silent regressions in that path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import render_with_fixtures, sample_narrative
from trailstory.models import Style

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_sample_narrative_has_required_trilingual_fields() -> None:
    n = sample_narrative()
    assert n.title.en and n.title.ru and n.title.de
    # ADR-014: paragraphs is now list[Paragraph]; flatten via the helper
    # so this smoke check still expresses "all three languages are present".
    flat = n.paragraphs_as_localized()
    assert flat.en and flat.ru and flat.de
    assert n.pull_quote.en and n.pull_quote.ru and n.pull_quote.de
    assert n.milestone.en and n.milestone.ru and n.milestone.de
    assert n.selected_photo_indices


def test_render_with_fixtures_writes_html(tmp_path: Path) -> None:
    out = render_with_fixtures(output_dir=tmp_path)
    assert out.is_file()
    assert out.suffix == ".html"
    text = out.read_text(encoding="utf-8")
    assert "Above the fog line" in text
    assert "data:image/jpeg;base64," in text


@pytest.mark.parametrize("style", list(Style))
def test_render_with_fixtures_matches_golden(tmp_path: Path, style: Style) -> None:
    """Catch any silent change to a style template, the elevation SVG,
    photo encoding pipeline, or fixture data. The render is deterministic
    given the same inputs, so byte-equality against
    ``tests/golden/test-render-<style>.html`` is the cheapest gate
    available.

    To update: ``make golden-update``
    """
    out = render_with_fixtures(output_dir=tmp_path, style=style)
    actual = out.read_bytes()
    expected = (GOLDEN_DIR / f"test-render-{style.value}.html").read_bytes()
    assert actual == expected, (
        f"rendered HTML drifted from tests/golden/test-render-{style.value}.html. "
        "If the change is intentional, regenerate the golden with: "
        "`make golden-update`"
    )
