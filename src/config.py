from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    line_channel_secret: str
    line_channel_access_token: str
    gemini_api_key: str
    neon_database_url: str
    gemini_model: str = "gemini-2.5-flash"
    command_model: str = "gemini-2.5-flash"
    bot_mention_name: str = "通訳AI"
    max_context_messages: int = 8
    max_group_languages: int = 5
    gemini_timeout_seconds: int = 10
    translation_retry: int = 2
    log_level: str = "INFO"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_monthly_id: str = ""
    free_quota_per_month: int = 50
    pro_quota_per_month: int = 8000
    subscription_frontend_base_url: str = ""
    checkout_api_base_url: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """環境変数から設定を読み込む薄いラッパー（既存実装と同等）。"""

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
        command_model=env.get("COMMAND_MODEL", env.get("GEMINI_MODEL", "gemini-2.5-flash")),
        bot_mention_name=env.get("BOT_MENTION_NAME", "通訳AI"),
        max_context_messages=int(env.get("MAX_CONTEXT_MESSAGES", "8")),
        max_group_languages=int(env.get("MAX_GROUP_LANGUAGES", "5")),
        gemini_timeout_seconds=int(env.get("GEMINI_TIMEOUT_SECONDS", "10")),
        translation_retry=int(env.get("TRANSLATION_RETRY", "2")),
        log_level=env.get("LOG_LEVEL", "INFO"),
        stripe_secret_key=env.get("STRIPE_SECRET_KEY", ""),
        stripe_webhook_secret=env.get("STRIPE_WEBHOOK_SECRET", ""),
        stripe_price_monthly_id=env.get("STRIPE_PRICE_MONTHLY_ID", ""),
        free_quota_per_month=int(env.get("FREE_QUOTA_PER_MONTH", "50")),
        pro_quota_per_month=int(env.get("PRO_QUOTA_PER_MONTH", "8000")),
        subscription_frontend_base_url=(
            env.get("SUBSCRIPTION_FRONTEND_BASE_URL")
        ),
        checkout_api_base_url=(
            env.get("CHECKOUT_API_BASE_URL")
        ),
    )
