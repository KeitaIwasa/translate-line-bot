from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, List, Optional, Sequence

import requests
from requests import HTTPError

from ..domain.models import CommandDecision, LanguageChoice
from ..domain.services.explicit_language_reset import is_explicit_language_reset
from .gemini_generation_config import build_generation_config

logger = logging.getLogger(__name__)

# Jev が言語設定と判定したあとに、追加・削除する言語だけを取り出す
LANGUAGE_SYSTEM_PROMPT = """
You extract language-setting details for a LINE multilingual translation bot.
The user's intent is already classified as language_settings. Do not reclassify it.

Input is a JSON object. Use user_message and current_languages.

Return:
- operation: "show" (list the current translation languages, or ask which language to add, without changing them), "reset_all" (clear languages and ask again), "add", "remove", or "add_and_remove"
- languages_to_add / languages_to_remove: languages named in the message. Prefer ISO 639-1 codes. Leave both empty for "show" and "reset_all".
- instruction_language: BCP-47 or ISO 639-1 of the user message
- ack_text: a short confirmation in the same language as the user message, without Markdown and without the bot mention. For "show", leave ack_text empty.

If the user is only asking which translation languages are currently set, use operation "show".
Use "reset_all" only when the user explicitly asks to clear or reset the language settings.
Do not use "reset_all" when the message does not name languages. A message such as "言語を追加したい" is not a reset: use operation "show" so the bot lists the current languages or asks which language to add, and leave both language lists empty.
Do not use "reset_all" for a question about the current languages.
""".strip()

# グループで使えなかったときの定型文。英語をベース原文にし、英語以外のときだけ翻訳させる
HOWTO_FALLBACK_TEXT = "Sorry, I can't answer that question. Please contact us here: https://kotori-ai.com/contact.html"

HOWTO_SYSTEM_PROMPT = f"""
You answer one question about KOTORI, a LINE multilingual translation bot.
The user's intent is already classified as howto. Do not reclassify it.
The answer is posted as a reply in the LINE group where the user mentioned KOTORI, so KOTORI is already in this group.

Write ack_text in the same language as the user message.
Use plain text only. Do not use Markdown.
Answer from the support guide. Keep it short.
If the guide does not support a reliable answer, set ack_text to this English sentence. Use it exactly as is when the user writes in English; otherwise translate it into the user's language and keep the URL unchanged:
{HOWTO_FALLBACK_TEXT}
Return instruction_language as BCP-47 or ISO 639-1.
""".strip()

# サポートガイド先頭の個人チャット向け指示は使わず、ガイド本文だけを渡す
_SUPPORT_GUIDE_MARKER = "# KOTORIガイド"

_LANGUAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["show", "reset_all", "add", "remove", "add_and_remove"],
        },
        "languages_to_add": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["code"],
            },
        },
        "languages_to_remove": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["code"],
            },
        },
        "instruction_language": {"type": "string"},
        "ack_text": {"type": "string"},
    },
    "required": ["operation", "instruction_language", "ack_text"],
}

_HOWTO_SCHEMA = {
    "type": "object",
    "properties": {
        "instruction_language": {"type": "string"},
        "ack_text": {"type": "string"},
    },
    "required": ["instruction_language", "ack_text"],
}

_VALID_OPERATIONS = {"show", "reset_all", "add", "remove", "add_and_remove"}


class GeminiMentionFollowUp:
    """言語設定の抽出と使い方回答だけを、既存の Gemini モデルへ1回ずつ聞く。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: int = 8,
        support_prompt_path: str = "",
        session: Optional[requests.Session] = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._support_guide = _guide_body(_load_text(support_prompt_path))
        self._session = session or requests.Session()

    def complete(self, action: str, router_input: str) -> CommandDecision:
        try:
            if action == "language_settings":
                return self._language_settings(router_input)
            if action == "howto":
                return self._howto(router_input)
        except Exception:
            logger.warning("Gemini mention follow-up failed | action=%s", action)
            return _error_decision()
        logger.warning("Gemini mention follow-up got unexpected action | action=%s", action)
        return _error_decision()

    def _language_settings(self, router_input: str) -> CommandDecision:
        data = self._generate(LANGUAGE_SYSTEM_PROMPT, _LANGUAGE_SCHEMA, router_input)
        operation = str(data.get("operation") or "").strip()
        if operation not in _VALID_OPERATIONS:
            operation = "show"
        # モデルが言語未指定を reset_all にしても、明示的なリセット以外は設定を消さない
        if operation == "reset_all" and not is_explicit_language_reset(_user_message(router_input)):
            operation = "show"
        return CommandDecision(
            action="language_settings",
            operation=operation,
            languages_to_add=_parse_language_list(data.get("languages_to_add")),
            languages_to_remove=_parse_language_list(data.get("languages_to_remove")),
            instruction_language=str(data.get("instruction_language") or "").strip(),
            ack_text=str(data.get("ack_text") or "").strip(),
        )

    def _howto(self, router_input: str) -> CommandDecision:
        system_prompt = HOWTO_SYSTEM_PROMPT
        if self._support_guide:
            system_prompt = f"{HOWTO_SYSTEM_PROMPT}\n\nSupport guide:\n{self._support_guide}"
        data = self._generate(system_prompt, _HOWTO_SCHEMA, router_input)
        return CommandDecision(
            action="howto",
            instruction_language=str(data.get("instruction_language") or "").strip(),
            ack_text=str(data.get("ack_text") or "").strip(),
        )

    def _generate(self, system_prompt: str, schema: dict, router_input: str) -> dict:
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": router_input}],
                }
            ],
            "generationConfig": build_generation_config(self._model, schema),
        }
        response = self._session.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
            params={"key": self._api_key},
            json=payload,
            timeout=self._timeout,
        )
        try:
            response.raise_for_status()
        except HTTPError as exc:
            raise RuntimeError(f"gemini_http_{response.status_code}") from exc
        body = response.json()
        try:
            part_text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("unexpected gemini follow-up response") from exc
        data = _parse_json_object(part_text)
        if not isinstance(data, dict):
            raise ValueError("gemini follow-up JSON was not an object")
        return data


def _error_decision() -> CommandDecision:
    return CommandDecision(action="error", instruction_language="", ack_text="")


def _parse_json_object(text: str) -> Any:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return json.loads(stripped)


def _parse_language_list(items: Any) -> List[LanguageChoice]:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return []
    parsed: List[LanguageChoice] = []
    for item in items:
        code = ""
        name = ""
        if isinstance(item, str):
            code = item.strip().lower()
            name = code
        elif isinstance(item, dict):
            code = str(item.get("code") or item.get("lang") or item.get("language") or "").strip().lower()
            name = str(item.get("name") or code).strip()
        if code:
            parsed.append(LanguageChoice(code=code, name=name or code))
    return parsed


def _user_message(router_input: str) -> str:
    """ルーター入力の JSON から user_message を取り出す。JSON でなければ原文を返す。"""

    try:
        data = json.loads(router_input)
    except Exception:
        return router_input
    if isinstance(data, dict) and isinstance(data.get("user_message"), str):
        return data["user_message"]
    return router_input


def _load_text(path: str) -> str:
    if not path:
        return ""
    prompt_path = Path(path)
    if not prompt_path.exists():
        logger.warning("Support prompt file not found: %s", path)
        return ""
    return prompt_path.read_text(encoding="utf-8")


def _guide_body(text: str) -> str:
    """個人チャット向けの前置き指示を除き、ガイド本文だけを返す。"""
    index = text.find(_SUPPORT_GUIDE_MARKER)
    if index < 0:
        return text
    return text[index:]
