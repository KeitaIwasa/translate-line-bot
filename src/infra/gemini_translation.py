from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Sequence

import requests

from src.domain.models import TranslationRequest, TranslationResult
from src.domain.ports import TranslationPort
from src.infra.gemini_errors import GeminiRateLimitError, GeminiRequestError
from src.infra.gemini_generation_config import build_generation_config
from src.infra.translation_schema import TRANSLATION_SCHEMA

logger = logging.getLogger(__name__)

UI_BATCH_SYSTEM = """
You translate short UI strings for a LINE bot subscription menu.
The user message is JSON with target_language and items[{id, text}].
Return JSON {"items":[{"id","text"}]} using the same ids, one translation each, in target_language.
Keep the meaning. Preserve numbers, dates, the plan names Free, Standard and Pro, and URLs.
Do not add explanations or extra items.
""".strip()

UI_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "text"],
            },
        }
    },
    "required": ["items"],
}

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
""".strip()


@dataclass(frozen=True)
class _SourceMessage:
    sender_name: str
    text: str
    timestamp: datetime


@dataclass(frozen=True)
class _ContextMessage:
    sender_name: str
    text: str
    timestamp: datetime


class GeminiTranslationAdapter(TranslationPort):
    """Gemini への I/O を担当するインフラ層のアダプタ。"""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._session = requests.Session()

    def translate(self, request: TranslationRequest) -> List[TranslationResult]:
        if not request.candidate_languages:
            return []

        source = _SourceMessage(
            sender_name=request.sender_name,
            text=request.message_text,
            timestamp=request.timestamp,
        )
        context = [
            _ContextMessage(sender_name=msg.sender_name, text=msg.text, timestamp=msg.timestamp)
            for msg in request.context_messages
        ]

        payload = self._build_payload(source, context, list(request.candidate_languages))
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"

        logger.debug(
            "Gemini request prepared | model=%s targets=%s context_count=%s",
            self._model,
            list(request.candidate_languages),
            len(context),
        )

        started = time.perf_counter()
        try:
            response = self._session.post(
                url,
                params={"key": self._api_key},
                json=payload,
                timeout=self._timeout,
            )
        finally:
            logger.info(
                "Gemini call | model=%s elapsed_ms=%.2f targets=%s",
                self._model,
                (time.perf_counter() - started) * 1000,
                len(request.candidate_languages),
            )
        try:
            response.raise_for_status()
        except requests.HTTPError:
            status_code = response.status_code
            logger.error(
                "Gemini request failed | model=%s status=%s",
                self._model,
                status_code,
            )
            if status_code == 429:
                raise GeminiRateLimitError(self._model) from None
            raise GeminiRequestError(self._model, status_code) from None

        body = response.json()
        try:
            candidate = body["candidates"][0]
            part_text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            logger.error("Gemini response format invalid | model=%s", self._model)
            raise ValueError("Unexpected Gemini response format") from exc

        data = json.loads(part_text)

        translations = data.get("translations", [])
        allowed = {lang.lower() for lang in request.candidate_languages}
        results = [
            TranslationResult(lang=item["lang"], text=item["text"])
            for item in translations
            if item.get("lang") and item.get("text") and item["lang"].lower() in allowed
        ]
        logger.info(
            "Gemini translations parsed | model=%s count=%s langs=%s",
            self._model,
            len(results),
            [item.lang for item in results],
        )
        return results

    def translate_ui_strings(self, texts: Sequence[str], target_language: str) -> List[str]:
        """複数のUI文言を1回のGemini呼び出しで翻訳する。失敗時は原文を返す。"""

        originals = [str(text) for text in texts]
        if not originals:
            return []
        normalized = (target_language or "").strip()
        if not normalized or normalized.lower().startswith("en"):
            return originals
        try:
            return self._translate_ui_strings_once(originals, normalized)
        except Exception:
            logger.warning(
                "Gemini UI batch translation failed; using source strings | model=%s count=%s",
                self._model,
                len(originals),
            )
            return originals

    def _translate_ui_strings_once(self, originals: List[str], target_language: str) -> List[str]:
        items = [{"id": str(index), "text": text} for index, text in enumerate(originals)]
        payload = {
            "systemInstruction": {"parts": [{"text": UI_BATCH_SYSTEM}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {"target_language": target_language, "items": items},
                                ensure_ascii=False,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": build_generation_config(self._model, UI_BATCH_SCHEMA),
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        started = time.perf_counter()
        response = self._session.post(
            url,
            params={"key": self._api_key},
            json=payload,
            timeout=self._timeout,
        )
        logger.info(
            "Gemini UI batch | model=%s elapsed_ms=%.2f count=%s",
            self._model,
            (time.perf_counter() - started) * 1000,
            len(originals),
        )
        response.raise_for_status()
        body = response.json()
        part_text = body["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(part_text)
        translated_items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(translated_items, list):
            raise ValueError("UI batch response missing items")
        by_id: dict[str, str] = {}
        for item in translated_items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            text = item.get("text")
            if item_id and isinstance(text, str) and text.strip():
                by_id[item_id] = text.strip()
        if not by_id:
            raise ValueError("UI batch response had no translations")
        return [by_id.get(str(index), originals[index]) for index in range(len(originals))]

    def _build_payload(
        self,
        source_message: _SourceMessage,
        context_messages: Iterable[_ContextMessage],
        target_languages: List[str],
    ) -> dict:
        def _truncate_with_ellipsis(text: str, limit: int) -> str:
            if len(text) <= limit:
                return text
            # オーバー分を省き、末尾に省略記号を付与して上限を守る
            return text[: max(limit - 3, 0)] + "..."

        def _format_timestamp(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        truncated_source_text = _truncate_with_ellipsis(source_message.text, 800)

        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "source_message": {
                                        "sender_name": source_message.sender_name,
                                        "text": truncated_source_text,
                                        "timestamp": _format_timestamp(source_message.timestamp),
                                    },
                                    "context_messages": [
                                        {
                                            "sender_name": msg.sender_name,
                                            "text": _truncate_with_ellipsis(msg.text, 250),
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
            "generationConfig": build_generation_config(self._model, TRANSLATION_SCHEMA),
        }
        return payload
