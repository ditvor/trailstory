from __future__ import annotations

from datetime import UTC, datetime

from trailstory.daylight import daylight_context, time_of_day_label

# ── time_of_day_label ───────────────────────────────────────────────────────


def test_morning_label_for_munich_summer_morning() -> None:
    """8 AM UTC in summer Munich is well after dawn — morning."""
    label = time_of_day_label(lat=48.137, lon=11.575, when=datetime(2025, 7, 15, 8, 0, tzinfo=UTC))
    assert label == "morning"


def test_midday_label_for_munich_summer_noon() -> None:
    """Around solar noon in summer Munich falls in the midday bucket."""
    # Solar noon in Munich on 2025-07-15 is about 11:23 UTC. ±1h window.
    label = time_of_day_label(
        lat=48.137, lon=11.575, when=datetime(2025, 7, 15, 11, 30, tzinfo=UTC)
    )
    assert label == "midday"


def test_afternoon_label_for_munich_summer_afternoon() -> None:
    label = time_of_day_label(lat=48.137, lon=11.575, when=datetime(2025, 7, 15, 15, 0, tzinfo=UTC))
    assert label == "afternoon"


def test_pre_dawn_label_for_munich_predawn() -> None:
    """2 AM UTC in summer Munich is well before sunrise — pre-dawn."""
    label = time_of_day_label(lat=48.137, lon=11.575, when=datetime(2025, 7, 15, 2, 0, tzinfo=UTC))
    assert label == "pre-dawn"


def test_evening_label_for_munich_late_summer_evening() -> None:
    """10 PM UTC in summer Munich is after sunset — evening."""
    label = time_of_day_label(lat=48.137, lon=11.575, when=datetime(2025, 7, 15, 22, 0, tzinfo=UTC))
    assert label == "evening"


def test_naive_datetime_treated_as_utc() -> None:
    """A naive datetime should match the same UTC datetime's label."""
    naive = time_of_day_label(lat=48.137, lon=11.575, when=datetime(2025, 7, 15, 8, 0))
    aware = time_of_day_label(lat=48.137, lon=11.575, when=datetime(2025, 7, 15, 8, 0, tzinfo=UTC))
    assert naive == aware


def test_polar_winter_returns_unknown() -> None:
    """Above the Arctic Circle in December there's no sunrise; soft-fail."""
    label = time_of_day_label(lat=78.92, lon=11.93, when=datetime(2025, 12, 21, 12, 0, tzinfo=UTC))
    # astral raises for polar night; we translate to "unknown".
    assert label == "unknown"


# ── daylight_context ────────────────────────────────────────────────────────


def test_daylight_context_collapses_same_bucket() -> None:
    """When start and end land in the same bucket, return just that label."""
    phrase = daylight_context(
        lat=48.137,
        lon=11.575,
        start=datetime(2025, 7, 15, 8, 0, tzinfo=UTC),
        end=datetime(2025, 7, 15, 9, 30, tzinfo=UTC),
    )
    assert phrase == "morning"


def test_daylight_context_joins_different_buckets() -> None:
    """Different buckets join with 'to'."""
    phrase = daylight_context(
        lat=48.137,
        lon=11.575,
        start=datetime(2025, 7, 15, 8, 0, tzinfo=UTC),
        end=datetime(2025, 7, 15, 15, 0, tzinfo=UTC),
    )
    assert phrase == "morning to afternoon"


def test_daylight_context_unknown_when_either_endpoint_fails() -> None:
    """If either endpoint can't be classified, the whole context is unknown."""
    phrase = daylight_context(
        lat=78.92,
        lon=11.93,
        start=datetime(2025, 12, 21, 10, 0, tzinfo=UTC),
        end=datetime(2025, 12, 21, 14, 0, tzinfo=UTC),
    )
    assert phrase == "unknown"
