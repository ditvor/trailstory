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
    # Vision describer model (Phase 3 / ADR-010). Same model family as
    # the ledger extractor by default — Haiku 4.5 has vision and the
    # per-photo describer task is small enough that Opus quality is
    # overkill. Override via VISION_MODEL env var.
    vision_model: str = "claude-haiku-4-5"
    # Master switch for the Phase 3 vision pass. Default on per ADR-010
    # — vision grounding is the whole point. Set USE_PHOTO_GROUNDING=0
    # to skip vision calls entirely (cheaper, faster, but the writer
    # loses photo-grounded specifics — equivalent to the Phase 2
    # contract). Useful for cost-sensitive batch runs or when an
    # operator wants to bisect a quality regression to the vision pass.
    use_photo_grounding: bool = True
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
