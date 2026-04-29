from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Waypoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float
    lon: float
    ele_m: float
    time: datetime | None = None


class GpxStats(BaseModel):
    distance_km: float = Field(ge=0)
    elevation_gain_m: float = Field(ge=0)
    duration_min: int = Field(ge=0)
    start_elev_m: float
    summit_elev_m: float
    waypoints: list[Waypoint]


class PhotoMeta(BaseModel):
    path: Path
    timestamp: datetime
    index: int = Field(ge=0)


class HikeInput(BaseModel):
    gpx_path: Path
    photos_dir: Path
    # Cap the parent's free-text seed at 1000 characters. Real seeds are
    # 2-3 sentences; this is a defensive bound against a malicious or
    # accidentally enormous payload bloating the LLM prompt and the
    # downstream cache key. Pydantic raises ValidationError on overflow,
    # which the CLI surfaces as a clean error message.
    seed_text: str = Field(max_length=1000)
    location_name: str | None = None


class LocalizedString(BaseModel):
    """One user-facing string in every supported language.

    The narrative pipeline produces every user-facing field in EN, RU, and
    DE in a single LLM call (see ADR-005). Adding a fourth language is one
    field here and one corresponding key in the prompt's JSON skeleton —
    no new flat fields elsewhere.
    """

    en: str
    ru: str
    de: str


class LocalizedParagraphs(BaseModel):
    """Sibling of ``LocalizedString`` for multi-paragraph fields.

    A ``LocalizedString`` can't carry list values, so paragraph blocks use
    this small parallel shape. Same language set, same evolution rules.
    """

    en: list[str]
    ru: list[str]
    de: list[str]


class NarrativeOutput(BaseModel):
    # Bump when the shape of NarrativeOutput changes in a way that would
    # invalidate cached entries (added/removed/renamed field, semantic
    # change to an existing one). The narrative cache (see
    # ``trailstory.llm.cache``) refuses to return entries whose
    # ``schema_version`` differs from the current value.
    schema_version: int = 2
    title: LocalizedString
    subtitle: LocalizedString
    paragraphs: LocalizedParagraphs
    pull_quote: LocalizedString
    milestone: LocalizedString
    selected_photo_indices: list[int]


class Memory(BaseModel):
    hike_input: HikeInput
    gpx_stats: GpxStats
    narrative: NarrativeOutput
    selected_photos: list[PhotoMeta]
