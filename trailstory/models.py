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
    seed_text: str
    baby_name: str
    baby_age_months: int = Field(ge=0)
    location_name: str | None = None


class NarrativeOutput(BaseModel):
    # Bump when the shape of NarrativeOutput changes in a way that would
    # invalidate cached entries (added/removed/renamed field, semantic
    # change to an existing one). The narrative cache (see
    # ``trailstory.llm.cache``) refuses to return entries whose
    # ``schema_version`` differs from the current value.
    schema_version: int = 1
    title_en: str
    title_ru: str
    subtitle_en: str
    subtitle_ru: str
    paragraphs_en: list[str]
    paragraphs_ru: list[str]
    pull_quote_en: str
    pull_quote_ru: str
    milestone_en: str
    milestone_ru: str
    selected_photo_indices: list[int]


class Memory(BaseModel):
    hike_input: HikeInput
    gpx_stats: GpxStats
    narrative: NarrativeOutput
    selected_photos: list[PhotoMeta]
