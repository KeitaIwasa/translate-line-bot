from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List

import requests

from .schema import TRANSLATION_SCHEMA

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """
You are an interpreting engine for a multilingual LINE group.

You receive a JSON object containing:

* "source_message": the message to be translated
* "context_messages": recent messages in the same group
* "target_languages": an array of language codes to translate into

Requirements:

* Use "source_message.text" as the text to translate.
* Use "context_messages" to understand the context and who is speaking to whom.
* Preserve user names (sender_name) exactly as they are; Do NOT translate them.
* Preserve mention strings (e.g., "@John") in their original form.
* Produce natural interpretations that match each user's tone and the conversational context.
* Do not copy, quote, or directly reproduce the source_message.text in the translation output; return only the translated text for each target language.
* Output only a JSON object that conforms to the specified JSON Schema.
* Do NOT include context_messages or target_languages in the output JSON.
"""


@dataclass(frozen=True)
class SourceMessage:
    sender_name: str
    text: str
    timestamp: datetime


@dataclass(frozen=True)
class ContextMessage:
    sender_name: str
    text: str
    timestamp: datetime


@dataclass(frozen=True)
class Translation:
    lang: str
    text: str


class GeminiRateLimitError(requests.HTTPError):
    """Raised when Gemini returns HTTP 429 Too Many Requests."""


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._session = requests.Session()

    def translate(
        self,
        source_message: SourceMessage,
        context_messages: Iterable[ContextMessage],
        target_languages: List[str],
    ) -> List[Translation]:
        if not target_languages:
            return []

        payload = self._build_payload(source_message, context_messages, target_languages)
        params = {"key": self._api_key}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"

        try:
            user_content_str = payload["contents"][0]["parts"][0]["text"]
            user_content_obj = json.loads(user_content_str)
            logger.info("Gemini request content decoded: %s", json.dumps(user_content_obj, ensure_ascii=False))
        except Exception:  # pylint: disable=broad-except
            logger.debug("Failed to decode Gemini request content for logging", exc_info=True)

        response = self._session.post(url, params=params, json=payload, timeout=self._timeout)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                raise GeminiRateLimitError(exc.response) from exc
            raise

        body = response.json()
        try:
            candidate = body["candidates"][0]
            part_text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Unexpected Gemini response format: {body}") from exc

        data = json.loads(part_text)
        logger.info("Gemini parsed translations: %s", json.dumps(data, ensure_ascii=False))
        translations = data.get("translations", [])
        allowed = {lang.lower() for lang in target_languages}
        parsed = [
            Translation(lang=item["lang"], text=item["text"])
            for item in translations
            if item.get("lang") and item.get("text") and item["lang"].lower() in allowed
        ]
        return parsed

    def _build_payload(
        self,
        source_message: SourceMessage,
        context_messages: Iterable[ContextMessage],
        target_languages: List[str],
    ) -> dict:
        def _format_timestamp(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": SYSTEM_INSTRUCTION.strip(),
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "source_message": {
                                        "sender_name": source_message.sender_name,
                                        "text": source_message.text,
                                        "timestamp": _format_timestamp(source_message.timestamp),
                                    },
                                    "context_messages": [
                                        {
                                            "sender_name": msg.sender_name,
                                            "text": msg.text,
                                            "timestamp": _format_timestamp(msg.timestamp),
                                        }
                                        for msg in context_messages
                                    ],
                                    "target_languages": target_languages,
                                },
                                ensure_ascii=False,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": TRANSLATION_SCHEMA,
            },
        }
        return payload
