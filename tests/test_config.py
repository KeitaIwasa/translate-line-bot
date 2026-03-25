from src.config import get_settings


def test_get_settings_reads_openai_env_vars(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_SUPPORT_MODEL", "gpt-5.2")
    monkeypatch.setenv("OPENAI_GROUP_MENTION_MODEL", "gpt-5.2")
    monkeypatch.setenv("OPENAI_GUARDRAIL_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("PRIVATE_CHAT_HISTORY_LIMIT", "7")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.openai_api_key == "sk-test"
    assert settings.openai_support_model == "gpt-5.2"
    assert settings.openai_group_mention_model == "gpt-5.2"
    assert settings.openai_guardrail_model == "gpt-4.1-mini"
    assert settings.private_chat_history_limit == 7


def test_get_settings_reads_contact_env_vars(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("CONTACT_TO_EMAIL", "contact@iwasadigital.com")
    monkeypatch.setenv("CONTACT_FROM_EMAIL", "no-reply@iwasadigital.com")
    monkeypatch.setenv("CONTACT_ALLOWED_ORIGINS", "https://kotori-ai.com,http://localhost:5500")
    monkeypatch.setenv("CONTACT_RATE_LIMIT_MAX", "9")
    monkeypatch.setenv("CONTACT_RATE_LIMIT_WINDOW_SECONDS", "120")
    monkeypatch.setenv("CONTACT_IP_HASH_SALT", "salt-123")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.contact_to_email == "contact@iwasadigital.com"
    assert settings.contact_from_email == "no-reply@iwasadigital.com"
    assert settings.contact_allowed_origins == "https://kotori-ai.com,http://localhost:5500"
    assert settings.contact_rate_limit_max == 9
    assert settings.contact_rate_limit_window_seconds == 120
    assert settings.contact_ip_hash_salt == "salt-123"
