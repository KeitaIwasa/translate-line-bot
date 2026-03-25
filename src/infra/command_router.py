from __future__ import annotations

import asyncio
import ast
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Sequence

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
- "subscription_menu": user wants to manage subscription via menu (show buttons).
- "subscription_cancel": user wants to cancel subscription; bot should ask confirmation.
- "subscription_upgrade": user wants to upgrade to pro plan (checkout redirect link).
- "unknown": anything else.

Constraints:
- Detect the language of the instruction and return it in BCP-47 (or ISO 639-1) as instruction_language.
- Produce ack_text in the same language as the instruction. The ack_text should be concise and confirm the action.
- Do NOT include the bot mention text in the ack_text.
- Do NOT echo the entire user message.
- For specific actions, tailor ack_text as follows:
  - pause: "I will pause the translation. Please mention me again when you want to resume." 
  - resume: "I will resume the translation."
  - subscription_menu: brief confirmation like "Opening subscription menu." in instruction language.
  - subscription_cancel: acknowledge that a cancel confirmation will be shown.
  - subscription_upgrade: acknowledge that an upgrade link will be provided.
""".strip()


SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "language_settings",
                "howto",
                "pause",
                "resume",
                "subscription_menu",
                "subscription_cancel",
                "subscription_upgrade",
                "unknown",
            ],
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
            headers={"x-goog-api-key": self._api_key},
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
        valid_actions = {
            "language_settings",
            "howto",
            "pause",
            "resume",
            "subscription_menu",
            "subscription_cancel",
            "subscription_upgrade",
            "unknown",
        }
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
                                        "subscription_menu": ["サブスク", "subscription", "plan", "billing"],
                                        "subscription_cancel": ["解約", "cancel subscription", "停止"],
                                        "subscription_upgrade": ["アップグレード", "upgrade to pro", "pro plan"],
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
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }


class OpenAIGroupMentionCommandRouter(CommandRouterPort):
    """OpenAI Agent SDK を使ってグループメンション操作を判定する。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_path: str,
        timeout_seconds: int = 10,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._prompt_path = prompt_path
        self._timeout_seconds = timeout_seconds
        self._instructions = self._load_prompt(prompt_path)

    def decide(self, text: str) -> CommandDecision:
        try:
            output = self._run_agent(text)
        except Exception:
            logger.exception("Group mention agent execution failed")
            return self._error_decision()
        decision = self._to_command_decision(output)
        if not decision:
            return self._error_decision()
        return decision

    def _run_agent(self, payload_text: str) -> Any:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")

        try:
            from agents import (
                Agent,
                ModelSettings,
                Runner,
                function_tool,
                set_default_openai_key,
            )
        except Exception as exc:
            raise RuntimeError("openai-agents is unavailable") from exc

        set_default_openai_key(self._api_key)

        def _payload(
            *,
            action: str,
            ack_text: str = "",
            instruction_language: str = "",
            operation: str = "",
            languages_to_add: List[str] | None = None,
            languages_to_remove: List[str] | None = None,
        ) -> Dict[str, Any]:
            return {
                "action": action,
                "instruction_language": instruction_language or "",
                "ack_text": ack_text or "",
                "operation": operation or "",
                "languages_to_add": list(languages_to_add or []),
                "languages_to_remove": list(languages_to_remove or []),
            }

        @function_tool
        def change_language_settings(
            operation: Literal["reset_all", "add", "remove", "add_and_remove"] = "reset_all",
            languages_to_add: List[str] | None = None,
            languages_to_remove: List[str] | None = None,
            ack_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(
                action="language_settings",
                operation=operation,
                languages_to_add=languages_to_add,
                languages_to_remove=languages_to_remove,
                ack_text=ack_text,
                instruction_language=instruction_language,
            )

        @function_tool
        def answer_help_or_question(
            answer_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(action="howto", ack_text=answer_text, instruction_language=instruction_language)

        @function_tool
        def pause_translation(
            ack_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(action="pause", ack_text=ack_text, instruction_language=instruction_language)

        @function_tool
        def resume_translation(
            ack_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(action="resume", ack_text=ack_text, instruction_language=instruction_language)

        @function_tool
        def show_subscription_menu(
            ack_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(action="subscription_menu", ack_text=ack_text, instruction_language=instruction_language)

        @function_tool
        def confirm_pro_cancellation(
            ack_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(action="subscription_cancel", ack_text=ack_text, instruction_language=instruction_language)

        @function_tool
        def show_pro_upgrade_link(
            ack_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(action="subscription_upgrade", ack_text=ack_text, instruction_language=instruction_language)

        @function_tool
        def unknown_instruction(
            ack_text: str = "",
            instruction_language: str = "",
        ) -> Dict[str, Any]:
            return _payload(action="unknown", ack_text=ack_text, instruction_language=instruction_language)

        agent = Agent(
            name="group mention command router",
            instructions=self._instructions,
            model=self._model,
            tools=[
                change_language_settings,
                answer_help_or_question,
                pause_translation,
                resume_translation,
                show_subscription_menu,
                confirm_pro_cancellation,
                show_pro_upgrade_link,
                unknown_instruction,
            ],
            tool_use_behavior="stop_on_first_tool",
            model_settings=ModelSettings(
                tool_choice="required",
                reasoning={"effort": "low"},
                parallel_tool_calls=False,
            ),
        )

        run_coro = Runner.run(
            agent,
            payload_text,
            max_turns=4,
        )
        if self._timeout_seconds and self._timeout_seconds > 0:
            run_coro = asyncio.wait_for(run_coro, timeout=self._timeout_seconds)
        result = self._run_async(run_coro)
        return getattr(result, "final_output", None)

    def _to_command_decision(self, raw_output: Any) -> CommandDecision | None:
        payload = self._normalize_output(raw_output)
        if not isinstance(payload, dict):
            preview = str(raw_output)
            logger.warning(
                "Unexpected router output type: %s preview=%s",
                type(raw_output).__name__,
                preview[:300],
            )
            return None

        action = str(payload.get("action") or "").strip()
        instruction_language = str(payload.get("instruction_language") or "").strip()
        ack_text = str(payload.get("ack_text") or "").strip()
        if action == "language_settings":
            operation = str(payload.get("operation") or "reset_all").strip()
            return CommandDecision(
                action="language_settings",
                operation=operation,
                languages_to_add=self._parse_language_list(payload.get("languages_to_add")),
                languages_to_remove=self._parse_language_list(payload.get("languages_to_remove")),
                instruction_language=instruction_language,
                ack_text=ack_text,
            )

        valid_actions = {
            "howto",
            "pause",
            "resume",
            "subscription_menu",
            "subscription_cancel",
            "subscription_upgrade",
            "unknown",
        }
        if action not in valid_actions:
            logger.warning("Unexpected action from group mention agent", extra={"action": action})
            return None
        return CommandDecision(
            action=action,
            instruction_language=instruction_language,
            ack_text=ack_text,
        )

    @staticmethod
    def _normalize_output(raw_output: Any) -> Any:
        if raw_output is None:
            return None
        if isinstance(raw_output, dict):
            return raw_output
        if isinstance(raw_output, str):
            stripped = raw_output.strip()
            if not stripped:
                return None
            try:
                return json.loads(stripped)
            except Exception:
                # stop_on_first_tool + output_type未設定時に str(dict) が返る場合の救済
                try:
                    parsed = ast.literal_eval(stripped)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return None
                return None
        if hasattr(raw_output, "model_dump"):
            try:
                return raw_output.model_dump()
            except Exception:
                return None
        if hasattr(raw_output, "__dict__"):
            return dict(raw_output.__dict__)
        return None

    @staticmethod
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

    @staticmethod
    def _error_decision() -> CommandDecision:
        return CommandDecision(action="error", instruction_language="", ack_text="")

    @staticmethod
    def _run_async(coro):
        try:
            return asyncio.run(coro)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

    @staticmethod
    def _load_prompt(path: str) -> str:
        prompt_path = Path(path)
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        logger.warning("Prompt file not found: %s", path)
        return (
            "You are KOTORI group mention command router. "
            "Always call exactly one tool and respond in the user's input language."
        )
