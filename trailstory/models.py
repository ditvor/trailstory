from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Style(StrEnum):
    """Visual treatment for the rendered memory page (see ADR-006).

    The same ``NarrativeOutput`` renders under any style — only the Jinja
    template and CSS bundle differ. Adding a new style is a templates-and-
    CSS PR plus one new enum value; no prompt or eval changes.

    v0 ships ``editorial`` (the Letter). ADR-015's roadmap adds Zine,
    Sunday, Postcard, Album as separate template-only PRs; their enum
    values land alongside their respective renderer PRs.
    The legacy ``log`` and ``encyclopedia`` values were removed in
    ADR-015 PR 1 — they were never surfaced in the picker and the v0
    product decision is to ship the five Trailpath styles only.
    """

    editorial = "editorial"


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

    Under ADR-014 (Phase 4) this shape is no longer the canonical paragraph
    representation — ``NarrativeOutput.paragraphs`` is now
    ``list[Paragraph]`` with sentence-level provenance. ``LocalizedParagraphs``
    survives as a small adapter (``paragraphs_as_localized``) on
    ``NarrativeOutput`` so existing flat-text consumers keep working without
    duplicating the join logic everywhere.
    """

    en: list[str]
    ru: list[str]
    de: list[str]


class ProvenanceSource(StrEnum):
    """Why a sentence is in the narrative (ADR-014, Phase 4).

    The writer tags every sentence with one of these values; the HTML
    template renders the tag as a subtle hover/tint so the user can see at
    a glance which sentences are seed-grounded vs inferred. Phase 4.1 will
    let the user click an INFERRED sentence and edit/remove it before
    publishing.
    """

    # Source quotes / explicitly states this sentence.
    SEED = "seed"
    # A photo's PhotoDescription supports this sentence.
    PHOTO = "photo"
    # A GPX-derived fact (date, distance, season) supports this sentence.
    GPX = "gpx"
    # Reasonable interpretation from ledger context — not stated outright
    # in any source. Tinted in the rendered HTML.
    INFERRED = "inferred"


class Provenance(BaseModel):
    """Per-sentence grounding tag (ADR-014, Phase 4)."""

    model_config = ConfigDict(frozen=True)

    source: ProvenanceSource
    # Short pointer back to the source: a seed quote span, a photo index,
    # a GPX field name, or a free-form note for INFERRED. Always non-empty
    # so the hover UI has something to show.
    reference: str = ""


class Sentence(BaseModel):
    """One sentence of the narrative, tri-lingual, with provenance.

    Sentences are language-aligned by construction: the writer produces
    EN/RU/DE for each sentence as a single unit, so ``text.en``,
    ``text.ru``, ``text.de`` are translations of each other, not
    independent prose. Each sentence carries one ``Provenance`` because
    the question "why is this sentence here?" has one answer regardless
    of which language the reader sees.
    """

    model_config = ConfigDict(frozen=True)

    text: LocalizedString
    provenance: Provenance


# A paragraph is just an ordered list of Sentences. No wrapper class on
# purpose — the structure carries no metadata of its own; paragraph
# boundaries are a layout concern surfaced to the renderer.
Paragraph = list[Sentence]


# Exactly this many chapters per narrative under ADR-015. The Trailpath
# layouts (Zine, Sunday, Postcard, Album) all assume a 6-chapter contract
# — six "stops" per hike — so the writer is constrained to produce
# exactly that count. A future ADR can relax this to a range if real
# usage shows awkward bunching on short or all-day hikes.
CHAPTER_COUNT: int = 6


class Chapter(BaseModel):
    """One stop in the hike — title, time, place, body, bound photo.

    Introduced under ADR-015 as the data shape the Trailpath layouts
    (Zine / Sunday / Postcard / Album) consume natively. Replaces the
    flat ``paragraphs`` + ``selected_photo_indices`` pair on
    ``NarrativeOutput``: a chapter envelope carries its own body
    (sentence-level provenance preserved from ADR-014) plus the
    metadata each layout needs (per-chapter time + place + a one-to-
    one bound photo).
    """

    model_config = ConfigDict(frozen=True)

    # Stable id ("arrival", "river", "dinner") used as DOM anchor and
    # for chapter-rail nav. Slugged from ``title.en`` at writer time.
    id: str
    # GPX-derived. The writer is instructed to pick the timestamp of the
    # photo bound to this chapter (its EXIF time, snapped to the nearest
    # GPX waypoint). Format "HH:MM" 24-hour.
    time: str
    # Locality near ``coord`` — typically a coarse fallback to
    # ``HikeInput.location_name`` until a reverse geocoder lands (see
    # ADR-015 follow-ups).
    place: LocalizedString
    # Latitude / longitude of the bound photo (its GPS EXIF, or the
    # nearest GPX waypoint when GPS is absent). Some layouts (the Zine
    # route postmark, the Album rubber-stamp date+location) read these
    # to anchor on-page decorative SVGs to a real point on the track.
    lat: float
    lon: float
    # Short noun phrase. The writer is told to keep this concrete (a
    # place-name, a beat-name); the rubric/judge layers gate it as part
    # of the faithfulness axis.
    title: LocalizedString
    # 2-4 sentences, ~80-120 EN words. Same ``Sentence`` shape used for
    # the per-sentence provenance UI (ADR-014).
    body: Paragraph
    # Index into the FULL ``PhotoMeta`` list the pipeline loaded. The
    # orchestrator (CLI / web pipeline) builds ``Memory.selected_photos``
    # by gathering these in chapter order, so by the time a renderer
    # sees a ``Memory`` the chapter at position i binds to
    # ``Memory.selected_photos[i]``.
    photo_index: int = Field(ge=0)


class NarrativeOutput(BaseModel):
    # Bump when the shape of NarrativeOutput changes in a way that would
    # invalidate cached entries (added/removed/renamed field, semantic
    # change to an existing one). The narrative cache (see
    # ``trailstory.llm.cache``) refuses to return entries whose
    # ``schema_version`` differs from the current value.
    # v4 (ADR-015): chapters replace flat paragraphs +
    # selected_photo_indices. v3 entries (Phase 4 sentence-leveled flat
    # paragraphs) cannot be loaded; cache misses on read.
    schema_version: int = 4
    title: LocalizedString
    subtitle: LocalizedString
    # Exactly :data:`CHAPTER_COUNT` chapters. Each is a self-contained
    # envelope with its own body, bound photo, and per-chapter
    # title/time/place metadata. The writer prompt enforces the count;
    # this validator surfaces a clean Pydantic error if a malformed
    # response sneaks through.
    chapters: list[Chapter] = Field(min_length=CHAPTER_COUNT, max_length=CHAPTER_COUNT)
    pull_quote: LocalizedString
    milestone: LocalizedString

    def paragraphs_as_localized(self) -> LocalizedParagraphs:
        """Flatten chapter bodies into per-language paragraph text.

        Computed view over ``chapters[*].body``: one paragraph per
        chapter, joined sentences per language. Used by code that does
        not care about chapter envelope or per-sentence provenance —
        the Instagram carousel, the eval rubric's paragraph-count and
        word-ratio checks, the Letter template's flat-text fallback.
        Code that DOES care (the chapter-aware Letter template, the new
        Trailpath layouts) walks ``chapters`` directly.
        """
        return LocalizedParagraphs(
            en=[" ".join(s.text.en for s in c.body) for c in self.chapters],
            ru=[" ".join(s.text.ru for s in c.body) for c in self.chapters],
            de=[" ".join(s.text.de for s in c.body) for c in self.chapters],
        )

    @property
    def selected_photo_indices(self) -> list[int]:
        """Indices of every chapter's bound photo, in chapter order.

        Computed view over ``chapters[*].photo_index``. Survives as a
        read-only accessor so callers that orchestrate the photo
        binding (the CLI, the web pipeline, the carousel renderer)
        keep their old contract: walk this list, gather the photos,
        pass them down. The list contains exactly ``CHAPTER_COUNT``
        entries by construction.
        """
        return [c.photo_index for c in self.chapters]


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
