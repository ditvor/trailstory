"""Sunrise / sunset and time-of-day labels for the ledger (ADR-015).

Wraps the ``astral`` library so the ledger can carry a human-readable
"how the hike sits in the day's light" phrase ("morning to early
afternoon") instead of leaving the writer to guess from a raw datetime.
Local computation only — no external API, no privacy implications.

The labels are intentionally coarse. Astral can give astronomical
twilight, civil dawn, golden hour and so on; the writer's audience is
grandparents, not astronomers. Seven buckets (pre-dawn, dawn, morning,
midday, afternoon, dusk, evening) keep the language natural while
still anchoring to real sunrise/sunset for the hike's date and
location.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from astral import LocationInfo
from astral.sun import sun

logger = logging.getLogger(__name__)


# Bucket widths around the day's key moments. Tuned so that an "8:00 AM
# in May" hike start lands in "morning" rather than "dawn".
_DAWN_HALFWIDTH = timedelta(minutes=30)
_MIDDAY_HALFWIDTH = timedelta(hours=1)
_DUSK_HALFWIDTH = timedelta(minutes=30)


def time_of_day_label(*, lat: float, lon: float, when: datetime) -> str:
    """Return a coarse label describing when ``when`` falls in the local day.

    Possible return values (deterministic order along the day):
    ``pre-dawn``, ``dawn``, ``morning``, ``midday``, ``afternoon``,
    ``dusk``, ``evening``. Returns ``"unknown"`` when astral cannot
    compute sunrise / noon / sunset for the given lat/lon and date
    (polar regions in winter, malformed inputs).

    Naive datetimes are interpreted as UTC. This is the same assumption
    the rest of the pipeline makes — GPX track waypoints arrive
    UTC-stamped, photo timestamps arrive naive, and we already compare
    them naive-to-naive elsewhere.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    info = LocationInfo(latitude=lat, longitude=lon, timezone="UTC")
    try:
        times = sun(info.observer, date=when.date(), tzinfo=UTC)
    except ValueError as exc:
        # astral raises ValueError for polar night/day ("Sun never reaches
        # ... below the horizon") and for malformed lat/lon inputs.
        # Soft-fail: ledger gets "unknown" and the writer falls back to
        # whatever time-of-day language the chronology beats provide.
        logger.info(
            "astral.sun could not compute solar times for lat=%s lon=%s on %s: %s",
            lat,
            lon,
            when.date(),
            exc,
        )
        return "unknown"

    sunrise: datetime = times["sunrise"]
    noon: datetime = times["noon"]
    sunset: datetime = times["sunset"]

    if when < sunrise - _DAWN_HALFWIDTH:
        return "pre-dawn"
    if when < sunrise + _DAWN_HALFWIDTH:
        return "dawn"
    if when < noon - _MIDDAY_HALFWIDTH:
        return "morning"
    if when < noon + _MIDDAY_HALFWIDTH:
        return "midday"
    if when < sunset - _DUSK_HALFWIDTH:
        return "afternoon"
    if when < sunset + _DUSK_HALFWIDTH:
        return "dusk"
    return "evening"


def daylight_context(*, lat: float, lon: float, start: datetime, end: datetime) -> str:
    """Return a short phrase summarising the hike's daylight bracket.

    Examples:
    - ``"morning to early afternoon"`` (start in morning, end in
      afternoon)
    - ``"morning"`` (both endpoints in the same bucket)
    - ``"unknown"`` (any solar lookup failed)

    The phrase is consumed by the writer prompt as a single string —
    designed to drop into a sentence without further formatting.
    """
    start_label = time_of_day_label(lat=lat, lon=lon, when=start)
    end_label = time_of_day_label(lat=lat, lon=lon, when=end)
    if start_label == "unknown" or end_label == "unknown":
        return "unknown"
    if start_label == end_label:
        return start_label
    return f"{start_label} to {end_label}"
