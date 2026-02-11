from src.config import get_settings


def test_get_settings_reads_openai_env_vars(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_SUPPORT_MODEL", "gpt-5.2")
    monkeypatch.setenv("OPENAI_GUARDRAIL_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("PRIVATE_CHAT_HISTORY_LIMIT", "7")

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.openai_api_key == "sk-test"
    assert settings.openai_support_model == "gpt-5.2"
    assert settings.openai_guardrail_model == "gpt-4.1-mini"
    assert settings.private_chat_history_limit == 7
