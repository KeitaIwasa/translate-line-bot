from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from ..domain.models import LanguageChoice, LanguagePreference
from ..domain.ports import LanguagePreferencePort

logger = logging.getLogger(__name__)

LANGUAGE_PREF_SYSTEM_PROMPT = """
You analyze a LINE group message to decide which languages the user wants to enable.
Return JSON only.
For each language:
- Give an ISO code (prefer 639-1, else BCP-47).
- Give a display name in the primary language only.
- Mark supported=true/false.
Provide confirm/completed/cancel sentences in the primary language only.
""".strip()

LANGUAGE_PREF_SCHEMA = {
    "type": "object",
    "properties": {
        "primaryLanguage": {"type": "string"},
        "languages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "supported": {"type": "boolean"},
                    "display": {
                        "type": "object",
                        "properties": {
                            "primary": {"type": "string"},
                        },
                        "required": ["primary"],
                    },
                },
                "required": ["code", "supported", "display"],
            },
        },
        "textBlocks": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "object",
                    "properties": {
                        "primary": {"type": "string"},
                    },
                    "required": ["primary"],
                },
                "completed": {
                    "type": "object",
                    "properties": {
                        "primary": {"type": "string"},
                    },
                    "required": ["primary"],
                },
                "cancel": {
                    "type": "object",
                    "properties": {
                        "primary": {"type": "string"},
                    },
                    "required": ["primary"],
                },
            },
            "required": ["confirm", "completed", "cancel"],
        },
        "buttonLabels": {
            "type": "object",
            "properties": {
                "confirm": {"type": "string"},
                "cancel": {"type": "string"},
            },
            "required": ["confirm", "cancel"],
        },
    },
    "required": ["primaryLanguage", "languages", "textBlocks", "buttonLabels"],
}


class LanguagePreferenceAdapter(LanguagePreferencePort):
    """Gemini を使った言語設定推定クライアント。"""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._session = requests.Session()

    def analyze(self, text: str) -> LanguagePreference | None:
        if not text or not text.strip():
            return None

        max_attempts = 2
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            payload = self._build_payload(text)
            try:
                response = self._session.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
                    params={"key": self._api_key},
                    json=payload,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                body = response.json()
                logger.debug("Gemini language preference raw response", extra={"body": body})

                try:
                    candidate = body["candidates"][0]
                    part_text = candidate["content"]["parts"][0]["text"]
                except (KeyError, IndexError) as exc:
                    raise ValueError(f"Unexpected Gemini response format: {body}") from exc

                data = json.loads(part_text)
                languages = [
                    LanguageChoice(
                        code=item.get("code", "").lower(),
                        name=item.get("display", {}).get("primary", "") or item.get("code", ""),
                    )
                    for item in data.get("languages", [])
                    if item.get("code")
                ]
                if not languages:
                    return None

                supported = [lang for lang, raw in zip(languages, data.get("languages", [])) if raw.get("supported", True)]
                unsupported = [lang for lang, raw in zip(languages, data.get("languages", [])) if not raw.get("supported", True)]

                text_blocks = data.get("textBlocks", {})
                buttons = data.get("buttonLabels", {})
                return LanguagePreference(
                    supported=supported,
                    unsupported=unsupported,
                    confirm_label=buttons.get("confirm", "完了"),
                    cancel_label=buttons.get("cancel", "変更する"),
                    confirm_text=_pick_primary(text_blocks.get("confirm")),
                    cancel_text=_pick_primary(text_blocks.get("cancel")),
                    completion_text=_pick_primary(text_blocks.get("completed")),
                    primary_language=data.get("primaryLanguage", "").lower(),
                )
            except Exception as exc:  # pylint: disable=broad-except
                last_error = exc
                logger.warning(
                    "Language preference request failed (attempt %s/%s)",
                    attempt + 1,
                    max_attempts,
                    exc_info=exc,
                )
                if attempt + 1 < max_attempts:
                    time.sleep(0.2 * (attempt + 1))

        if last_error:
            raise last_error
        return None

    def _build_payload(self, message_text: str) -> Dict:
        return {
            "systemInstruction": {"parts": [{"text": LANGUAGE_PREF_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "message": message_text,
                                    "requirements": {
                                        "languages": "Extract ISO codes and primary display name only (max 5).",
                                        "texts": "Provide confirm/completed/cancel strings in primary only.",
                                    },
                                }
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": LANGUAGE_PREF_SCHEMA,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }


def _pick_primary(block: Optional[Dict]) -> str:
    block = block or {}
    return block.get("primary", "") or block.get("english", "") or block.get("thai", "")
