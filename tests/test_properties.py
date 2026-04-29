"""Property-based regression guards for small load-bearing helpers.

Hypothesis explores the input space of helpers that are otherwise covered
only by hand-picked unit tests. These tests encode invariants that must
hold for any well-formed input — slug shape, text-wrap reversibility,
elevation-profile bounds.
"""

from __future__ import annotations

import re
from datetime import date
from itertools import pairwise

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import ImageFont

from trailstory.cli import _derive_slug, _slugify
from trailstory.gpx import elevation_profile
from trailstory.models import GpxStats, Waypoint
from trailstory.renderers.instagram import _wrap_text

SLUG_RE = re.compile(r"^[a-z0-9-]*$")
DERIVED_SLUG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")

# Default Pillow font: cheap to load and deterministic across hosts.
_DEFAULT_FONT = ImageFont.load_default(size=24)


# ── _slugify ─────────────────────────────────────────────────────────────────


@given(st.text())
def test_slugify_output_is_url_safe(text: str) -> None:
    """For any input, the slug only contains a-z, 0-9, and hyphens."""
    assert SLUG_RE.match(_slugify(text)) is not None


# ── _derive_slug ─────────────────────────────────────────────────────────────


@given(
    hike_date=st.dates(min_value=date(1970, 1, 1), max_value=date(9999, 12, 31)),
    location=st.one_of(st.none(), st.text(max_size=80)),
)
def test_derive_slug_is_non_empty_and_starts_with_iso_date(
    hike_date: date, location: str | None
) -> None:
    """``_derive_slug`` must always produce a non-empty slug that begins
    with an ISO-8601 date prefix; the location half can be empty."""
    slug = _derive_slug(hike_date, location)
    assert slug
    assert DERIVED_SLUG_RE.match(slug) is not None


# ── _wrap_text ───────────────────────────────────────────────────────────────


_WRAP_TEXT_ALPHABET = st.characters(
    categories=("Ll", "Lu", "Nd"),
    include_characters=" ",
)


@given(
    text=st.text(alphabet=_WRAP_TEXT_ALPHABET, max_size=200),
    max_width=st.integers(min_value=20, max_value=2000),
)
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_wrap_text_is_word_preserving(text: str, max_width: int) -> None:
    """Joining the wrapped lines must reproduce the original word sequence,
    and no individual line may contain a literal newline character."""
    lines = _wrap_text(text, _DEFAULT_FONT, max_width=max_width)
    assert " ".join(lines).split() == text.split()
    for line in lines:
        assert "\n" not in line


# ── elevation_profile ────────────────────────────────────────────────────────


_FINITE_LAT = st.floats(min_value=-89.0, max_value=89.0, allow_nan=False, allow_infinity=False)
_FINITE_LON = st.floats(min_value=-179.0, max_value=179.0, allow_nan=False, allow_infinity=False)
_FINITE_ELE = st.floats(min_value=-500.0, max_value=9000.0, allow_nan=False, allow_infinity=False)


@st.composite
def _waypoint_lists(draw: st.DrawFn, *, min_size: int = 2, max_size: int = 30) -> list[Waypoint]:
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    return [
        Waypoint(
            lat=draw(_FINITE_LAT),
            lon=draw(_FINITE_LON),
            ele_m=draw(_FINITE_ELE),
            time=None,
        )
        for _ in range(n)
    ]


@given(waypoints=_waypoint_lists(), n=st.integers(min_value=2, max_value=200))
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_elevation_profile_invariants(waypoints: list[Waypoint], n: int) -> None:
    """For any track, ``elevation_profile`` returns exactly ``n`` points
    whose x is monotonically non-decreasing and whose y is in [0, 1]."""
    elevs = [w.ele_m for w in waypoints]
    stats = GpxStats(
        distance_km=0.0,
        elevation_gain_m=0.0,
        duration_min=0,
        start_elev_m=min(elevs),
        summit_elev_m=max(elevs),
        waypoints=waypoints,
    )
    profile = elevation_profile(stats, n=n)
    assert len(profile) == n
    xs = [x for x, _ in profile]
    ys = [y for _, y in profile]
    for a, b in pairwise(xs):
        assert a <= b
    for y in ys:
        assert 0.0 <= y <= 1.0
