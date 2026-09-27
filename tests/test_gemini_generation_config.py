from src.infra.gemini_generation_config import build_generation_config


SCHEMA = {"type": "object"}


def test_gemini_3_models_use_minimal_thinking_level_without_legacy_sampling_options():
    config = build_generation_config("gemini-3.5-flash-lite", SCHEMA)

    assert config == {
        "responseMimeType": "application/json",
        "responseSchema": SCHEMA,
        "thinkingConfig": {"thinkingLevel": "minimal"},
    }


def test_latest_flash_alias_uses_gemini_3_compatible_config():
    config = build_generation_config("gemini-flash-latest", SCHEMA)

    assert config["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert "temperature" not in config
    assert "thinkingBudget" not in config["thinkingConfig"]


def test_gemini_2_5_models_keep_legacy_compatible_config():
    config = build_generation_config("gemini-2.5-flash", SCHEMA)

    assert config == {
        "temperature": 0.2,
        "responseMimeType": "application/json",
        "responseSchema": SCHEMA,
        "thinkingConfig": {"thinkingBudget": 0},
    }
