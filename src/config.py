from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class Settings:
    line_channel_secret: str
    line_channel_access_token: str
    gemini_api_key: str
    neon_database_url: str
    gemini_model: str = "gemini-2.5-flash"
    max_context_messages: int = 20
    gemini_timeout_seconds: int = 15
    translation_retry: int = 3
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load strongly typed settings from environment variables."""

    env = os.environ

    required = {
        "LINE_CHANNEL_SECRET": env.get("LINE_CHANNEL_SECRET"),
        "LINE_CHANNEL_ACCESS_TOKEN": env.get("LINE_CHANNEL_ACCESS_TOKEN"),
        "GEMINI_API_KEY": env.get("GEMINI_API_KEY"),
        "NEON_DATABASE_URL": env.get("NEON_DATABASE_URL"),
    }

    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return Settings(
        line_channel_secret=required["LINE_CHANNEL_SECRET"],
        line_channel_access_token=required["LINE_CHANNEL_ACCESS_TOKEN"],
        gemini_api_key=required["GEMINI_API_KEY"],
        neon_database_url=required["NEON_DATABASE_URL"],
        gemini_model=env.get("GEMINI_MODEL", "gemini-2.5-flash"),
        max_context_messages=int(env.get("MAX_CONTEXT_MESSAGES", "20")),
        gemini_timeout_seconds=int(env.get("GEMINI_TIMEOUT_SECONDS", "15")),
        translation_retry=int(env.get("TRANSLATION_RETRY", "3")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )
