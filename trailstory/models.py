from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Style(StrEnum):
    """Visual treatment for the rendered memory page (see ADR-006).

    The same ``NarrativeOutput`` renders under any style — only the Jinja
    template and CSS bundle differ. Adding a fourth style is a templates-
    and-CSS PR plus one new enum value; no prompt or eval changes.
    """

    editorial = "editorial"
    log = "log"
    encyclopedia = "encyclopedia"


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


class PhotoDescription(BaseModel):
    """Structured description of a single photo, produced by Claude vision.

    Phase 3 / ADR-010 grounding source for the ledger extractor: the
    extractor sees these alongside the seed text so its
    ``chronology[*].objects_mentioned`` reflects what the photos actually
    show, not just what the seed names. Conservative by design — the
    describer prompt only emits what is visible, not inferred
    relationships or motives.
    """

    model_config = ConfigDict(frozen=True)

    # Coarse count + brief descriptors ("a man with a beard", "a baby in a
    # green hat"). Identities, names, and inferred relationships are NOT
    # emitted by the describer — only what a viewer can see at a glance.
    people_visible: list[str] = Field(default_factory=list)
    # Concrete objects in frame ("a picnic blanket", "a lake", "a calisthenics
    # pull-up bar"). Generic backgrounds (sky, grass) are skipped.
    objects_visible: list[str] = Field(default_factory=list)
    # Hints about where the photo was taken ("parking lot with cars",
    # "lakeside with mountains in the distance"). Empty when ambiguous.
    location_clues: list[str] = Field(default_factory=list)
    # Hints about season / time of day / weather ("bright midday sun",
    # "spring foliage", "overcast sky"). Empty when ambiguous.
    season_clues: list[str] = Field(default_factory=list)
    # Notes on body language and facial expression visible to a viewer
    # ("smiling", "concentrating on the pull-up bar"). Not emotion
    # inferred from context — only what is on the face.
    body_language_notes: list[str] = Field(default_factory=list)


class PhotoMeta(BaseModel):
    path: Path
    timestamp: datetime
    index: int = Field(ge=0)
    # Optional vision-derived description (ADR-010). None when the
    # vision pass is disabled (``Settings.use_photo_grounding=False``)
    # or has not yet run for this photo. Frozen ``PhotoMeta`` with a
    # default allows the existing ``load_photos`` pipeline to keep its
    # contract; ``describe_photos`` produces updated copies via
    # ``model_copy(update={...})``.
    description: PhotoDescription | None = None


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


# ── FactLedger (ADR-009, Phase 2 of the faithfulness initiative) ─────────────
#
# Structured intermediate representation between raw hike inputs and the
# writer prompt. Built in a two-pass flow: a cheap extractor LLM reads
# seed_text + GPX + photo timestamps and emits the LLM-derived subset
# (people, weather, chronology); Python merges in the deterministic fields
# (GPX stats, derived season, photo count) and validates the whole.
#
# The writer LLM consumes ONLY this ledger — no raw seed text — so it is
# structurally unable to introduce a duck if the ledger does not mention
# one. Ledger contents are English; the writer translates to RU+DE per
# ADR-005.


class Person(BaseModel):
    """One person involved in the hike, as extracted by the ledger pass."""

    model_config = ConfigDict(frozen=True)

    name: str
    # Optional short role descriptor — "wife", "baby in carrier", "hiking
    # partner", "father-in-law". Helps the writer place the person in the
    # narrative without inventing a relationship the seed did not state.
    role: str | None = None


class Beat(BaseModel):
    """One moment in the hike's chronology.

    The ``objects_mentioned`` field is the load-bearing fabrication guard:
    the writer is told it may name specific objects only if they appear in
    some beat's ``objects_mentioned``. Empty list = generic prose only
    (sky, water, path), no named animals/foods/places/objects for that
    beat.
    """

    model_config = ConfigDict(frozen=True)

    time_of_day: str  # "morning", "midday", "afternoon", "evening", or specific.
    activity: str
    emotion: str | None = None
    objects_mentioned: list[str] = Field(default_factory=list)


class FactLedger(BaseModel):
    """Source of truth for the writer pass under ADR-009.

    Built by :func:`trailstory.llm.narrative.extract_ledger` from a hike
    input + GPX + photos. Some fields are LLM-derived (people, weather,
    chronology) and some are deterministic (GPX stats, season, photo
    count); both halves end up here for the writer's single-input
    contract.
    """

    model_config = ConfigDict(frozen=True)

    # LLM-derived ----------------------------------------------------------
    people: list[Person]
    weather: str  # short phrase from seed, e.g. "amazing weather" or "unknown".
    chronology: list[Beat]

    # Deterministic from inputs --------------------------------------------
    where: str  # location name (HikeInput.location_name or fallback).
    when: datetime  # hike start (first timed GPX waypoint, or epoch fallback).
    season: str  # verbose phrase, see _infer_date_and_season (ADR-008).
    duration_min: int = Field(ge=0)
    distance_km: float = Field(ge=0)
    elevation_gain_m: float = Field(ge=0)
    summit_elev_m: float
    n_photos: int = Field(ge=1)


class Memory(BaseModel):
    hike_input: HikeInput
    gpx_stats: GpxStats
    narrative: NarrativeOutput
    selected_photos: list[PhotoMeta]
    style: Style = Style.editorial
