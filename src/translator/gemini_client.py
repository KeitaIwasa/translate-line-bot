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
You are a translation engine for a multi-language LINE group.

You receive a JSON object with:
- "source_message": the message to translate, including sender information.
- "context_messages": recent messages in the same group, each with sender information.
- "target_languages": an array of language codes to translate into.

Requirements:
- Use "source_message.text" as the text to translate.
- Use "context_messages" only to understand the context and who is talking to whom.
- Preserve user names (sender_name) as they are. Do NOT translate names.
- Preserve mention strings (e.g., "@John") exactly as they appear in the source text.
- Use natural, context-aware translations.
- Do NOT copy, quote, or echo the source_message.text in any translation output; provide only the translated text for each target language.
- Return ONLY a JSON object that matches the given JSON Schema.
- Do NOT include context_messages or target_languages in the output JSON.
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

        logger.debug("Sending translation request to Gemini", extra={"target_langs": target_languages})
        response = self._session.post(url, params=params, json=payload, timeout=self._timeout)
        response.raise_for_status()

        body = response.json()
        logger.debug("Gemini raw response", extra={"body": body})

        try:
            candidate = body["candidates"][0]
            part_text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Unexpected Gemini response format: {body}") from exc

        data = json.loads(part_text)
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
                                        "timestamp": source_message.timestamp.isoformat(),
                                    },
                                    "context_messages": [
                                        {
                                            "sender_name": msg.sender_name,
                                            "text": msg.text,
                                            "timestamp": msg.timestamp.isoformat(),
                                        }
                                        for msg in context_messages
                                    ],
                                    "target_languages": target_languages,
                                }
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
