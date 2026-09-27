from __future__ import annotations

from typing import Any, Dict


_MODERN_MODEL_ALIASES = {"gemini-flash-latest"}


def build_generation_config(model: str, response_schema: Dict[str, Any]) -> Dict[str, Any]:
    """モデル世代に合わせたGenerateContent用のgenerationConfigを作る。"""

    config: Dict[str, Any] = {
        "responseMimeType": "application/json",
        "responseSchema": response_schema,
    }

    normalized_model = model.strip().lower()
    is_gemini_3 = normalized_model.startswith("gemini-3") or normalized_model in _MODERN_MODEL_ALIASES
    if is_gemini_3:
        # Gemini 3系では旧thinkingBudgetとtemperatureを送らず、最小思考レベルを指定する。
        config["thinkingConfig"] = {"thinkingLevel": "minimal"}
    else:
        # Gemini 2.5系および既存モデルの挙動は維持する。
        config["temperature"] = 0.2
        config["thinkingConfig"] = {"thinkingBudget": 0}

    return config
