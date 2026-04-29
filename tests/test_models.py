"""Tests for ``trailstory.models``.

Coverage focus is on the validation contract — i.e. fields where Pydantic is
load-bearing for safety (length caps, non-negative numerics) rather than
re-testing Pydantic itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trailstory.models import HikeInput


def _base_kwargs(seed_text: str) -> dict[str, object]:
    return {
        "gpx_path": Path("hike.gpx"),
        "photos_dir": Path("photos"),
        "seed_text": seed_text,
    }


def test_hike_input_accepts_seed_text_at_limit() -> None:
    """Exactly 1000 characters must validate — boundary is inclusive."""
    seed = "a" * 1000
    hike = HikeInput(**_base_kwargs(seed))
    assert len(hike.seed_text) == 1000


def test_hike_input_rejects_seed_text_over_limit() -> None:
    """A seed longer than the cap must raise ``ValidationError`` — defends
    the LLM prompt against an oversized payload."""
    seed = "a" * 1001
    with pytest.raises(ValidationError) as excinfo:
        HikeInput(**_base_kwargs(seed))
    # Confirm the error names the offending field rather than something generic.
    assert "seed_text" in str(excinfo.value)
