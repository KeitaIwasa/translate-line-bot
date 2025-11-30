from __future__ import annotations

import json
import logging
from typing import Dict, List

import requests
from requests import HTTPError

from ..domain.models import CommandDecision, LanguageChoice
from ..domain.ports import CommandRouterPort

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are a command classifier for a LINE multilingual translation bot.

Input: a free-form message that mentions the bot and contains an instruction.
Goal: decide which operation the bot should perform.

Actions:
- "language_settings": user wants to change translation languages.
  - operation values: "reset_all" (reset and ask for all languages), "add", "remove", "add_and_remove".
  - languages_to_add: list of languages to newly enable.
  - languages_to_remove: list of languages to disable.
- "howto": user asks how to use the bot.
- "pause": temporarily pause translation until resumed.
- "resume": resume translation.
- "unknown": anything else.

Constraints:
- Detect the language of the instruction and return it in BCP-47 (or ISO 639-1) as instruction_language.
- Produce ack_text in the same language as the instruction. The ack_text should be concise and confirm the action.
- Do NOT include the bot mention text in the ack_text.
- Do NOT echo the entire user message.
""".strip()


SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["language_settings", "howto", "pause", "resume", "unknown"],
        },
        "instruction_language": {"type": "string"},
        "operation": {
            "type": "string",
            "enum": ["reset_all", "add", "remove", "add_and_remove"],
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
        "ack_text": {"type": "string"},
    },
    "required": ["action", "instruction_language", "ack_text"],
}


class GeminiCommandRouter(CommandRouterPort):
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._session = requests.Session()

    def decide(self, text: str) -> CommandDecision:
        payload = self._build_payload(text)
        response = self._session.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
            params={"key": self._api_key},
            json=payload,
            timeout=self._timeout,
        )
        try:
            response.raise_for_status()
        except HTTPError:
            logger.error(
                "Command router request failed: status=%s body=%s",
                response.status_code,
                response.text[:800],
            )
            return self._unknown_decision()

        body = response.json()
        logger.debug("command router raw response", extra={"body": body})

        try:
            candidate = body["candidates"][0]
            part_text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Unexpected command router response: {body}") from exc

        try:
            data = json.loads(part_text)
        except Exception:
            logger.error("Command router JSON parse failed", extra={"part_text": part_text})
            return self._unknown_decision()

        def _parse_lang_list(items: List[Dict] | None) -> List[LanguageChoice]:
            if not items:
                return []
            results: List[LanguageChoice] = []
            for item in items:
                code = (item.get("code") or "").strip().lower()
                name = item.get("name") or code
                if code:
                    results.append(LanguageChoice(code=code, name=name))
            return results

        decision = CommandDecision(
            action=data.get("action", "unknown"),
            operation=data.get("operation", ""),
            languages_to_add=_parse_lang_list(data.get("languages_to_add")),
            languages_to_remove=_parse_lang_list(data.get("languages_to_remove")),
            instruction_language=data.get("instruction_language", ""),
            ack_text=data.get("ack_text", ""),
        )
        valid_actions = {"language_settings", "howto", "pause", "resume", "unknown"}
        if decision.action not in valid_actions:
            return self._unknown_decision()
        return decision

    @staticmethod
    def _unknown_decision() -> CommandDecision:
        return CommandDecision(action="unknown", instruction_language="", ack_text="")

    def _build_payload(self, message_text: str) -> Dict:
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "message": message_text,
                                    "hints": {
                                        "language_settings": [
                                            "言語設定を変更", "言語を追加", "言語を削除", "reset languages", "add Spanish", "remove Japanese",
                                        ],
                                        "howto": ["使い方", "how to", "help"],
                                        "pause": ["翻訳停止", "pause translation"],
                                        "resume": ["翻訳再開", "resume translation"],
                                    },
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
                "responseSchema": SCHEMA,
            },
        }
