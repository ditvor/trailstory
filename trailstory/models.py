from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Style(StrEnum):
    """Visual treatment for the rendered memory page (see ADR-006, ADR-021).

    The same ``NarrativeOutput`` renders under any style — only the Jinja
    template and CSS bundle differ. The enum lists the full product
    lineup; only members in :data:`BUILT_STYLES` have a template under
    ``templates/styles/`` today. Building a planned style is a
    templates-and-CSS PR plus adding the member to ``BUILT_STYLES``; no
    prompt or eval changes.
    """

    letter = "letter"
    zine = "zine"
    sunday = "sunday"
    postcard = "postcard"
    album = "album"


# Styles whose renderer template exists. The renderer refuses the rest,
# the CLI only offers these, and the web picker shows the rest as SOON.
BUILT_STYLES: frozenset[Style] = frozenset({Style.letter, Style.zine})


class Waypoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float
    lon: float
    ele_m: float
    time: datetime | None = None


class TrackShape(StrEnum):
    """Topology of the hike's track (ADR-015).

    Distinguishing these matters for the writer: an out-and-back affords
    a "on the way back" beat, a loop affords a "completing the circle"
    beat, a point-to-point affords a "from A to B" framing. Detection is
    heuristic (start↔end proximity, then a bounding-box-span vs
    half-total-distance ratio to split out-and-back from loop — see
    ``trailstory.gpx._classify_track_shape``); when the track is too
    short or has only a single waypoint, we fall back to
    ``point_to_point`` as the safest neutral value.
    """

    loop = "loop"
    out_and_back = "out_and_back"
    point_to_point = "point_to_point"


class Pause(BaseModel):
    """One rest moment in the hike — a cluster of low-velocity waypoints.

    Detected in :mod:`trailstory.gpx` by walking waypoint pairs, computing
    instantaneous velocity, and clustering consecutive points where the
    walker was essentially stationary (< 0.3 m/s). Clusters under 5 min
    are dropped as GPS noise. The writer uses pauses to ground "we
    stopped" beats in the actual track rather than inventing them.
    """

    model_config = ConfigDict(frozen=True)

    # Cumulative distance along the track where the pause began.
    at_km: float = Field(ge=0)
    # Duration in whole minutes; clusters under 5 min are filtered upstream.
    duration_min: int = Field(ge=0)
    # Location of the pause (midpoint of the cluster) for downstream use
    # by reverse-geocoding or display. Not stored as a Waypoint to keep
    # Pause independent of the timestamp field.
    lat: float
    lon: float
    ele_m: float


class PhotoPosition(BaseModel):
    """A photo's position along the GPX track, derived after the fact.

    Built by :func:`trailstory.gpx.correlate_photos_to_track` using
    either the photo's EXIF GPS (preferred, when present) or the photo's
    EXIF timestamp matched to the nearest timed waypoint. The ledger
    surfaces these so the writer can ground per-beat language ("we
    paused around the 3.5 km mark") in the actual track instead of
    guessing. Photos that cannot be matched at all (no GPS, no timed
    waypoints) are simply absent from the position list.
    """

    model_config = ConfigDict(frozen=True)

    photo_index: int = Field(ge=0)
    km_along_track: float = Field(ge=0)
    ele_m: float


class GpxStats(BaseModel):
    distance_km: float = Field(ge=0)
    elevation_gain_m: float = Field(ge=0)
    # ADR-015: descent in metres, computed from gpxpy's uphill_downhill.
    # For loops and out-and-backs roughly equals gain; for point-to-point
    # tracks they diverge meaningfully and the difference grounds the
    # writer's "long descent" vs "rolling profile" framing.
    elevation_loss_m: float = Field(default=0.0, ge=0)
    duration_min: int = Field(ge=0)
    start_elev_m: float
    summit_elev_m: float
    # ADR-015: optional human-readable track name from the GPX file's
    # ``<name>`` tag (often a route name like "Wallberg via Setzberg"
    # or a peak name). Surfaced to the ledger so the writer has a
    # place name to lean on when the user didn't provide one.
    track_name: str | None = None
    # ADR-015: topology classification. ``point_to_point`` is the
    # default fallback; the detector in :mod:`trailstory.gpx` upgrades
    # to ``loop`` or ``out_and_back`` when the endpoints meet.
    track_shape: TrackShape = TrackShape.point_to_point
    # ADR-015: detected pauses (>=5 min). Empty list when no pause was
    # detected, when the GPX has no timestamps, or when the track is
    # too sparse for velocity clustering to work.
    pauses: list[Pause] = Field(default_factory=list)
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

    # ADR-018: shape version for the vision cache (ADR-012). Bump when the
    # describer's output shape changes so stale on-disk entries invalidate
    # instead of validating to silent defaults. Started at 1 with the
    # ADR-018 enriched fields (interactions / legible_text / scene_type /
    # light_and_color).
    schema_version: int = 1
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
    # ADR-018: how people physically relate to / carry one another, when
    # unambiguous ("an adult holding a baby in their arms", "wearing a
    # child carrier", "a child on an adult's shoulders"). Orientation-free
    # by contract: the describer prompt forbids front/back/chest/hip, and
    # ``photos._scrub_orientation`` strips any that slip through — the
    # spike behind ADR-018 showed every model guesses carry orientation
    # unreliably. Grounds the carry fact without the unreliable modifier.
    interactions: list[str] = Field(default_factory=list)
    # ADR-018: text genuinely legible in the photo — trail signs, summit
    # markers, route names, place labels — transcribed verbatim. Empty
    # when there is no text or it is too small / blurry to read. A summit
    # sign or route name here can corroborate the GPX location / track_name.
    legible_text: list[str] = Field(default_factory=list)
    # ADR-018: one short phrase for the dominant setting ("summit vista",
    # "lakeside", "forest trail", "trailhead / parking", "rest / picnic
    # spot"). None when genuinely unclear. Helps the writer place a beat.
    scene_type: str | None = None
    # ADR-018: one short faithful phrase on light quality + dominant
    # palette ("bright midday sun, hard shadows, green canopy"). Only what
    # is visible — no mood. None when unclear. Lets the writer render the
    # scene specifically without inventing.
    light_and_color: str | None = None


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
    # ADR-015: GPS coordinates read from EXIF before the strip-on-output
    # step in ``load_photos``. The resized JPEG written to disk still has
    # the GPS sub-IFD stripped — the privacy contract for the embedded
    # base64 photo in the HTML output is unchanged. These fields carry
    # the coordinates into Python only, where they feed the ledger's
    # photo↔track correlation. ``None`` when the photo has no GPS or
    # when the EXIF GPS tags are malformed.
    gps_lat: float | None = None
    gps_lon: float | None = None


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


class NarrativeOutput(BaseModel):
    # Bump when the shape of NarrativeOutput changes in a way that would
    # invalidate cached entries (added/removed/renamed field, semantic
    # change to an existing one). The narrative cache (see
    # ``trailstory.llm.cache``) refuses to return entries whose
    # ``schema_version`` differs from the current value.
    # v3 (ADR-014, Phase 4): paragraphs are now list[Paragraph] with
    # sentence-level provenance. v2 entries (the old LocalizedParagraphs
    # shape) cannot be loaded; cache misses on read.
    # v4 (ADR-015): the underlying FactLedger gained track_name,
    # track_shape, elevation_loss_m, day_of_week, daylight_context,
    # pauses, and photo_positions. The output shape is unchanged but
    # the writer's prompt now references these fields, so cached v3
    # narratives no longer reflect what the current writer would
    # produce. Bumping forces cache invalidation.
    # v5 (ADR-016): the ledger gained verbatim_user_phrases and the
    # writer prompt gained the voice-tightening register, anti-pattern
    # rules, and paired examples. Output shape unchanged; cached v4
    # narratives no longer reflect the current writer's voice.
    schema_version: int = 5
    title: LocalizedString
    subtitle: LocalizedString
    # Paragraphs are an ordered list of paragraphs; each paragraph is an
    # ordered list of sentences; each sentence carries tri-lingual text +
    # a single provenance tag. Joined per language for rendering via
    # :meth:`paragraphs_as_localized` so existing flat-text consumers
    # (rubric, instagram carousel) don't have to walk the structure.
    paragraphs: list[Paragraph]
    pull_quote: LocalizedString
    milestone: LocalizedString
    selected_photo_indices: list[int]

    def paragraphs_as_localized(self) -> LocalizedParagraphs:
        """Flatten the sentence-leveled paragraphs into per-language text.

        Joins sentences with a single space within each paragraph and
        returns one ``LocalizedParagraphs`` whose ``en`` / ``ru`` / ``de``
        lists have one string per paragraph. Used by code that does not
        care about provenance (the carousel renderer, the rubric's
        paragraph-count check, legacy templates) — code that DOES care
        (the HTML renderer, the Phase 4.1 builder edit mode) walks
        ``paragraphs`` directly.
        """
        return LocalizedParagraphs(
            en=[" ".join(s.text.en for s in p) for p in self.paragraphs],
            ru=[" ".join(s.text.ru for s in p) for p in self.paragraphs],
            de=[" ".join(s.text.de for s in p) for p in self.paragraphs],
        )


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

    ADR-015 expansion: the deterministic half now also carries
    ``track_name``, ``track_shape``, ``elevation_loss_m``,
    ``day_of_week``, ``daylight_context``, ``pauses``, and
    ``photo_positions`` — all computed in Python from the inputs the
    writer otherwise has no access to (per-photo GPS, sunrise/sunset
    math, waypoint velocity clustering). Denser ledger → less room for
    the writer to invent specifics.
    """

    model_config = ConfigDict(frozen=True)

    # LLM-derived ----------------------------------------------------------
    people: list[Person]
    weather: str  # short phrase from seed, e.g. "amazing weather" or "unknown".
    chronology: list[Beat]
    # ADR-016: 2-4 short phrases (≤ 8 words each) copied
    # character-for-character from the seed text by the extractor, then
    # verified as literal seed substrings in Python — an LLM's "verbatim"
    # cannot be trusted, so the guarantee is enforced after the call. The
    # writer must weave at least one into the prose: verbatim in the
    # language the hiker wrote it in, rendered faithfully in the other
    # two. Empty when the seed is too thin to quote.
    verbatim_user_phrases: list[str] = Field(default_factory=list)

    # Deterministic from inputs --------------------------------------------
    where: str  # location name (HikeInput.location_name or fallback).
    when: datetime  # hike start (first timed GPX waypoint, or epoch fallback).
    season: str  # verbose phrase, see _infer_date_and_season (ADR-008).
    duration_min: int = Field(ge=0)
    distance_km: float = Field(ge=0)
    elevation_gain_m: float = Field(ge=0)
    summit_elev_m: float
    n_photos: int = Field(ge=1)

    # ADR-015 deterministic expansion --------------------------------------
    # Track name from the GPX <name> tag (often a route name like
    # "Wallberg via Setzberg"). ``None`` when the file doesn't carry one
    # — the writer falls back to ``where`` in that case.
    track_name: str | None = None
    # Topology classification (loop / out_and_back / point_to_point).
    # Default point_to_point is the safest neutral framing when the
    # detector can't decide. See :class:`TrackShape`.
    track_shape: TrackShape = TrackShape.point_to_point
    # Descent in metres, complementing ``elevation_gain_m``. For loops
    # and out-and-backs roughly equals gain; for point-to-point tracks
    # they diverge and the gap grounds the writer's "long descent" /
    # "rolling profile" framing.
    elevation_loss_m: float = Field(default=0.0, ge=0)
    # Day name ("Saturday", "Sunday"). Cheap to derive, sometimes
    # surfaces a small narrative beat ("a Saturday walk").
    day_of_week: str = "unknown"
    # Coarse daylight bracket for the hike — see
    # :func:`trailstory.daylight.daylight_context`. Examples: "morning",
    # "morning to early afternoon", "afternoon to evening". "unknown"
    # when sunrise/sunset can't be resolved (polar regions, missing
    # location).
    daylight_context: str = "unknown"
    # Rest moments detected from waypoint velocity clustering. Empty
    # list when no pause was detected, when the GPX has no timestamps,
    # or when the track is too sparse for clustering to work.
    pauses: list[Pause] = Field(default_factory=list)
    # Per-photo positions along the track, derived from EXIF GPS or
    # timestamp matching. Photos with no usable match are absent rather
    # than represented as a guess.
    photo_positions: list[PhotoPosition] = Field(default_factory=list)


class PoiMatch(BaseModel):
    """A hiker beat resolved to a real named OSM feature (ADR-019).

    Produced deterministically by :mod:`trailstory.poi`: the hiker's
    landmark beat ("the wax-figure church") matched conservatively to a
    single named OpenStreetMap feature of the right category near the track
    ("Mühlfeldkirche"). The name is a sourced fact (OSM, ODbL), not the
    model's invention — it flows into the place stitch as grounded input.
    """

    model_config = ConfigDict(frozen=True)

    # The hiker's own beat that matched (verbatim from the ledger).
    beat: str
    # The OSM ``name`` tag of the matched feature.
    name: str
    # Normalised category label ("church", "lake", "peak", …).
    category: str


class PlaceContext(BaseModel):
    """The "about this place" block (ADR-017).

    A short tri-lingual note giving the reader a sense of where the hike
    happened. The ``summary`` is produced by a dedicated LLM "stitch" call
    (``trailstory.llm.place``) that may use external knowledge ONLY in the
    form of a supplied, citable reference extract (reverse-geocode →
    Wikipedia) plus the hiker's own ledger-grounded place beats — never the
    model's own memory.

    Carried on :class:`Memory`, deliberately NOT on :class:`NarrativeOutput`:
    it owns its own source attribution, must not bump the narrative
    ``schema_version``, and is resolved by a separate, optional pass. The
    field defaults to ``None`` everywhere, so a render without place context
    (the default) is unchanged.
    """

    model_config = ConfigDict(frozen=True)

    town: str
    # Coarse wider area ("Bavarian Prealps", "Upper Bavaria") or None.
    region: str | None = None
    # The stitched 2-3 sentence note, EN / RU / DE.
    summary: LocalizedString
    # The hiker beats the stitch reported weaving in — a cheap audit hook
    # mirroring sentence-level provenance (ADR-014). Not rendered.
    used_hiker_details: list[str] = Field(default_factory=list)
    # CC BY-SA attribution for the reference extract, rendered as a source
    # link under the block. ``None`` when the extract was empty (the
    # town-only fallback) — nothing to attribute.
    source_url: str | None = None
    source_title: str | None = None
    # ADR-019: hiker beats resolved to real named OSM features. Empty unless
    # POI resolution ran (``--poi`` / ``Settings.use_poi_resolution``) and
    # matched. Non-empty triggers an OpenStreetMap (ODbL) credit in the
    # rendered source line.
    named_landmarks: list[PoiMatch] = Field(default_factory=list)


class Memory(BaseModel):
    hike_input: HikeInput
    gpx_stats: GpxStats
    narrative: NarrativeOutput
    selected_photos: list[PhotoMeta]
    style: Style = Style.letter
    # ADR-017: optional "about this place" block. ``None`` (the default)
    # when the feature is off (no ``--place`` flag) or when geocoding /
    # the stitch soft-failed. Never required to render.
    place_context: PlaceContext | None = None
