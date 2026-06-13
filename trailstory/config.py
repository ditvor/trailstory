from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: SecretStr
    # Default narrative writer model — see docs/adr/002-narrative-model-choice.md.
    model: str = "claude-opus-4-7"
    # Ledger extractor model (Phase 2 / ADR-009). A cheap, fast model is
    # plenty for the structured-fact-extraction task; Opus quality is
    # wasted on a few hundred tokens of JSON. Override via LEDGER_MODEL
    # env var. Defaulting to the latest Haiku class as of ship time;
    # bump when a newer fast model lands.
    ledger_model: str = "claude-haiku-4-5"
    # Vision describer model (Phase 3 / ADR-010; default revised under
    # ADR-018). Sonnet by default: the ADR-018 spike showed Haiku
    # misreads fine detail that matters (it called a child carrier a
    # "dog"; Sonnet read it correctly), and the enriched describer's new
    # fields (interactions, legible_text) lean on exactly that fine
    # detail. The per-photo cost is small and the describer task is
    # bounded. Override via VISION_MODEL env var (drop back to
    # "claude-haiku-4-5" for cost-sensitive batch runs).
    vision_model: str = "claude-sonnet-4-6"
    # Master switch for the Phase 3 vision pass. Default on per ADR-010
    # — vision grounding is the whole point. Set USE_PHOTO_GROUNDING=0
    # to skip vision calls entirely (cheaper, faster, but the writer
    # loses photo-grounded specifics — equivalent to the Phase 2
    # contract). Useful for cost-sensitive batch runs or when an
    # operator wants to bisect a quality regression to the vision pass.
    use_photo_grounding: bool = True
    # Phase 3.2 / ADR-013: parallelism for the vision pass. Each photo
    # gets its own thread; the SDK is thread-safe and the GIL releases on
    # HTTP wait. Default 4 = a 6-photo hike completes in ~1 photo's worth
    # of wall time + coordination overhead. Lower if rate-limited.
    vision_concurrency: int = Field(
        default=4,
        ge=1,
        description="Max parallel vision describer calls per render.",
    )
    # Phase 2.5 / ADR-011: verifier ceiling on writer-self-reported
    # INFERRED-sentence share. If the writer's first draft exceeds this
    # share, the orchestrator regenerates once with feedback. 0.5 keeps
    # the writer honest without making the prose stiff. Set to 1.0 to
    # disable the verifier entirely (one writer call always).
    max_inferred_ratio: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Ceiling on the writer's self-reported INFERRED-sentence ratio "
            "before the Phase 2.5 verifier regenerates the draft. 1.0 disables."
        ),
    )
    # Place-context stitch model (ADR-017). The "about this place" block is
    # a constrained stitching task — connect a supplied reference extract
    # with the hiker's own ledger beats — not open creative writing, so a
    # Haiku-class model is the default. Override via PLACE_MODEL. Bump if
    # the tri-lingual register (RU/DE in particular) needs more horsepower.
    place_model: str = "claude-haiku-4-5"
    # Master switch for the ADR-017 place-context pass. Off by default:
    # reverse-geocoding sends the hike's coordinates to an external service
    # (a location disclosure), and "privacy as wedge" means the user opts
    # in explicitly — the CLI's --place flag, or USE_PLACE_CONTEXT=1.
    use_place_context: bool = False
    output_dir: Path = Path("./output")
    log_level: str = "INFO"

    photo_max_edge: int = Field(
        default=1800,
        description=(
            "Longest edge in pixels for resized photos. Sets the upper bound "
            "for HTML-embedded base64 JPEGs."
        ),
    )
    photo_quality: int = Field(
        default=90,
        description="JPEG quality (1-95) used when re-encoding resized photos.",
    )
    instagram_quality: int = Field(
        default=90,
        description="JPEG quality (1-95) used when writing Instagram carousel slides.",
    )
    narrative_max_tokens: int = Field(
        default=4096,
        description="Upper bound on tokens requested for the narrative completion.",
    )
    narrative_max_retries: int = Field(
        default=3,
        description=(
            "Total Anthropic API attempts (including the first) before "
            "giving up on rate-limit retries."
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = [
            ".".join(str(p) for p in err["loc"]) for err in exc.errors() if err["type"] == "missing"
        ]
        if "anthropic_api_key" in missing:
            sys.stderr.write(
                "error: ANTHROPIC_API_KEY is not set.\n"
                "  Set it in your environment or add it to a .env file "
                "(see .env.example).\n"
                "  Get a key at https://console.anthropic.com/settings/keys\n"
            )
            raise SystemExit(2) from exc
        raise
