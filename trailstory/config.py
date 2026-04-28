from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: SecretStr
    # Default narrative model — see docs/adr/002-narrative-model-choice.md.
    model: str = "claude-opus-4-7"
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
