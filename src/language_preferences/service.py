from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

LANGUAGE_PREF_SYSTEM_PROMPT = """
You analyze chat messages from LINE groups to determine which languages the user wants to enable for translation.
Return a structured JSON payload only.
For each detected language you must:
- Provide an ISO language code (ISO 639-1 preferred, fall back to BCP-47).
- Provide display names in the detected primary language, English, and Thai.
- Indicate if the language is supported for translation (supported=true/false).
Also craft multi-lingual sentences that will appear inside confirm/completion/cancel prompts.
The primary text should be written in the language that dominated the input message.
The English and Thai text should always be provided even if the user never mentioned those languages.
Use concise natural sentences.
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
                            "english": {"type": "string"},
                            "thai": {"type": "string"},
                        },
                        "required": ["primary", "english", "thai"],
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
                        "english": {"type": "string"},
                        "thai": {"type": "string"},
                    },
                    "required": ["primary", "english", "thai"],
                },
                "completed": {
                    "type": "object",
                    "properties": {
                        "primary": {"type": "string"},
                        "english": {"type": "string"},
                        "thai": {"type": "string"},
                    },
                    "required": ["primary", "english", "thai"],
                },
                "cancel": {
                    "type": "object",
                    "properties": {
                        "primary": {"type": "string"},
                        "english": {"type": "string"},
                        "thai": {"type": "string"},
                    },
                    "required": ["primary", "english", "thai"],
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


@dataclass(frozen=True)
class LanguageOption:
    code: str
    primary_name: str
    english_name: str
    thai_name: str
    supported: bool


@dataclass(frozen=True)
class TextSet:
    primary: str
    english: str
    thai: str

    def as_lines(self) -> List[str]:
        return [line for line in (self.primary, self.english, self.thai) if line]


@dataclass(frozen=True)
class LanguagePreferenceResult:
    primary_language: str
    languages: List[LanguageOption]
    confirm_text: TextSet
    completed_text: TextSet
    cancel_text: TextSet
    confirm_label: str
    cancel_label: str

    @property
    def supported_languages(self) -> List[LanguageOption]:
        return [lang for lang in self.languages if lang.supported]

    @property
    def unsupported_languages(self) -> List[LanguageOption]:
        return [lang for lang in self.languages if not lang.supported]


class LanguagePreferenceService:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._session = requests.Session()

    def analyze(self, text: str) -> Optional[LanguagePreferenceResult]:
        if not text or not text.strip():
            return None

        start = time.monotonic()
        max_attempts = 2
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            remaining = self._timeout - (time.monotonic() - start)
            if remaining <= 1:
                break

            payload = self._build_payload(text)
            try:
                response = self._session.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
                    params={"key": self._api_key},
                    json=payload,
                    timeout=remaining,
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
                    LanguageOption(
                        code=item.get("code", "").lower(),
                        primary_name=item.get("display", {}).get("primary", ""),
                        english_name=item.get("display", {}).get("english", ""),
                        thai_name=item.get("display", {}).get("thai", ""),
                        supported=bool(item.get("supported", True)),
                    )
                    for item in data.get("languages", [])
                    if item.get("code")
                ]
                if not languages:
                    return None

                text_blocks = data.get("textBlocks", {})
                buttons = data.get("buttonLabels", {})
                result = LanguagePreferenceResult(
                    primary_language=data.get("primaryLanguage", languages[0].code),
                    languages=languages,
                    confirm_text=_parse_text_set(text_blocks.get("confirm")),
                    completed_text=_parse_text_set(text_blocks.get("completed")),
                    cancel_text=_parse_text_set(text_blocks.get("cancel")),
                    confirm_label=buttons.get("confirm", "完了"),
                    cancel_label=buttons.get("cancel", "変更する"),
                )
                return result
            except Exception as exc:  # pylint: disable=broad-except
                last_error = exc
                logger.warning(
                    "Language preference request failed (attempt %s/%s)",
                    attempt + 1,
                    max_attempts,
                    exc_info=exc,
                )
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
                                        "languages": "Extract ISO codes and native names.",
                                        "texts": "Provide confirm/completed/cancel strings in primary, English, Thai.",
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
            },
        }


def _parse_text_set(payload: Optional[Dict]) -> TextSet:
    payload = payload or {}
    return TextSet(
        primary=payload.get("primary", ""),
        english=payload.get("english", ""),
        thai=payload.get("thai", ""),
    )
