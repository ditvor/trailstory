from __future__ import annotations

import sys
from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: SecretStr
    model: str = "claude-sonnet-4-6"
    output_dir: Path = Path("./output")
    log_level: str = "INFO"

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
