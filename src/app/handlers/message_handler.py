from __future__ import annotations

import base64
import json
import logging
import re
import time
import zlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from ...domain import models
from ...domain.ports import (
    CommandRouterPort,
    LanguagePreferencePort,
    LinePort,
    MessageRepositoryPort,
)
from ...domain.services.translation_service import TranslationService
from ...domain.services.interface_translation_service import InterfaceTranslationService
from ...domain.services.language_detection_service import LanguageDetectionService
from ...infra.gemini_translation import GeminiRateLimitError
from ...presentation.reply_formatter import (
    MAX_REPLY_LENGTH,
    build_translation_reply,
    strip_source_echo,
)
from .postback_handler import _build_cancel_message, _build_completion_message

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = "You have reached the rate limit. Please try again later."
_last_rate_limit_message: Dict[str, str] = {}

USAGE_MESSAGE_JA = "言語設定をしたあと、任意の言語でメッセージでやり取りをしてください。その都度このボットが各言語に翻訳した文章を送信します。"
UNKNOWN_INSTRUCTION_JA = (
    "このボットをメンションして操作を行いたい場合は、再びメンションして、以下のうちのいずれかを指示してください。\n"
    "- 言語設定の変更\n- 使い方説明\n- 翻訳停止"
)

GROUP_PROMPT_MESSAGE = (
    "I'm a multilingual translation bot. Please tell me the languages you want to translate to.\n\n"
    "多言語翻訳ボットです。翻訳したい言語を教えてください。\n\n"
    "我是一个多语言翻译机器人。请告诉我你想要翻译成哪些语言。\n\n"
    "ฉันเป็นบอทแปลหลายภาษา กรุณาบอกฉันว่าคุณต้องการแปลเป็นภาษาใดบ้าง\n\n"
    "ex) English, 中文, 日本語, ไทย"
)
DIRECT_GREETING = (
    "Thanks for adding me! Please invite me into a group so I can help with multilingual translation."
)
LANGUAGE_ANALYSIS_FALLBACK = (
    "ごめんなさい、翻訳する言語の確認に失敗しました。数秒おいてから、翻訳したい言語をカンマ区切りで送ってください。\n"
    "Sorry, I couldn't detect your languages. Please resend after a few seconds (e.g., English, 日本語, 中文, ไทย).\n"
    "ขออภัย ไม่สามารถระบุภาษาได้ กรุณาลองส่งมาใหม่อีกครั้ง (ตัวอย่าง: English, 日本語, 中文, ไทย)"
)


class MessageHandler:
    """message イベントのユースケースを担当。"""

    def __init__(
        self,
        line_client: LinePort,
        translation_service: TranslationService,
        interface_translation: InterfaceTranslationService,
        language_detector: LanguageDetectionService,
        language_pref_service: LanguagePreferencePort,
        command_router: CommandRouterPort,
        repo: MessageRepositoryPort,
        max_context_messages: int,
        translation_retry: int,
        bot_mention_name: str,
    ) -> None:
        self._line = line_client
        self._translation = translation_service
        self._interface_translation = interface_translation
        self._lang_detector = language_detector
        self._lang_pref = language_pref_service
        self._command_router = command_router
        self._repo = repo
        self._max_context = max_context_messages
        self._translation_retry = translation_retry
        self._bot_mention_name = bot_mention_name

    def handle(self, event: models.MessageEvent) -> None:
        if not event.reply_token:
            return

        # 1: 個チャットではグループ招待を案内
        if event.sender_type == "user" and (not event.group_id or event.group_id == event.user_id):
            self._line.reply_text(event.reply_token, DIRECT_GREETING)
            self._record_message(event, sender_name=event.user_id or "Unknown")
            return

        if not event.group_id or not event.user_id:
            return

        self._repo.ensure_group_member(event.group_id, event.user_id)

        timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        sender_name = self._resolve_sender_name(event)

        handled = False
        try:
            command_text = self._extract_command_text(event.text)
            if command_text:
                handled = self._handle_command(event, command_text)
            else:
                handled = self._handle_translation_flow(
                    event,
                    sender_name,
                    translation_enabled=self._repo.is_translation_enabled(event.group_id),
                )
        except GeminiRateLimitError:
            logger.warning("Gemini rate limited; notifying user")
            self._send_rate_limit_notice(event)
            handled = True
        except Exception:
            logger.exception("Message handling failed")
        finally:
            try:
                self._record_message(event, sender_name=sender_name, timestamp=timestamp)
            except Exception:
                logger.exception("Failed to persist message")

        return None if handled else None

    # --- internal helpers ---
    def _attempt_language_enrollment(self, event: models.MessageEvent) -> bool:
        logger.info(
            "Analyzing language preferences",
            extra={"group_id": event.group_id, "user_id": event.user_id, "text": event.text[:120]},
        )
        try:
            result = self._lang_pref.analyze(event.text)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to analyze language preferences: %s", exc)
            if event.reply_token:
                self._line.reply_text(event.reply_token, LANGUAGE_ANALYSIS_FALLBACK)
            return True

        if not result:
            logger.info("Language analysis returned no result", extra={"user_id": event.user_id})
            if event.reply_token:
                self._line.reply_text(event.reply_token, LANGUAGE_ANALYSIS_FALLBACK)
            return True

        supported = result.supported
        unsupported = result.unsupported
        logger.info(
            "Language analysis outcome",
            extra={
                "user_id": event.user_id,
                "supported": [lang.code for lang in supported],
                "unsupported": [lang.code for lang in unsupported],
            },
        )

        messages: List[Dict] = []
        if unsupported:
            messages.append({"type": "text", "text": self._format_unsupported_message(unsupported)})

        # 対応言語がなければ未対応メッセージだけ返して終了
        if not supported:
            if messages and event.reply_token:
                self._line.reply_messages(event.reply_token, messages)
            return True

        prompt_texts = self._prepare_language_prompt_texts(supported, result)
        confirm_payload = self._encode_postback_payload(
            {
                "kind": "language_confirm",
                "action": "confirm",
                "languages": [{"code": lang.code, "name": lang.name} for lang in supported],
                "primary_language": prompt_texts["primary_language"],
                "completion_text": prompt_texts["completion_text"],
            }
        )
        cancel_payload = self._encode_postback_payload(
            {
                "kind": "language_confirm",
                "action": "cancel",
                "primary_language": prompt_texts["primary_language"],
                "cancel_text": prompt_texts["cancel_text"],
            }
        )

        confirm_text = prompt_texts["confirm_text"]
        template_message = {
            "type": "template",
            "altText": "Confirm interpretation languages",
            "template": {
                "type": "confirm",
                "text": confirm_text,
                "actions": [
                    {"type": "postback", "label": f"🆗 {result.confirm_label}", "data": confirm_payload},
                    {"type": "postback", "label": f"↩️ {result.cancel_label}", "data": cancel_payload},
                ],
            },
        }

        messages.append(template_message)
        if event.reply_token:
            self._line.reply_messages(event.reply_token, messages)
        self._repo.record_language_prompt(event.group_id)
        self._repo.set_translation_enabled(event.group_id, False)
        logger.info(
            "Language enrollment prompt sent",
            extra={"group_id": event.group_id, "user_id": event.user_id, "prompted_langs": [lang.code for lang in supported]},
        )
        return True

    # --- command mode helpers ---
    def _extract_command_text(self, text: str) -> Optional[str]:
        if not text:
            return None
        name = self._bot_mention_name
        if not name:
            return None
        # メンションとしての @<bot name> が含まれているときだけコマンド扱いする
        pattern = rf"@\s*{re.escape(name)}"
        if not re.search(pattern, text, flags=re.IGNORECASE):
            return None
        stripped = re.sub(pattern, " ", text, count=1, flags=re.IGNORECASE)
        stripped = re.sub(r"\s{2,}", " ", stripped).strip()
        stripped = stripped.lstrip("-—–:：、，,。.!！?？ ")
        return stripped or ""

    def _handle_command(self, event: models.MessageEvent, command_text: str) -> bool:
        decision = self._command_router.decide(command_text)
        action = decision.action or "unknown"

        if action == "language_settings":
            return self._handle_language_settings(event, decision, command_text)

        if action == "howto":
            message = self._build_usage_response(decision.instruction_language, event.group_id)
            if event.reply_token and message:
                self._line.reply_text(event.reply_token, message)
            # 説明後は翻訳を再開
            self._repo.set_translation_enabled(event.group_id, True)
            return True

        if action == "pause":
            self._repo.set_translation_enabled(event.group_id, False)
            base_ack = decision.ack_text or "翻訳を一時停止します。再開するときはもう一度メンションしてください。"
            ack = self._build_multilingual_interface_message(base_ack, event.group_id)
            if event.reply_token:
                self._line.reply_text(event.reply_token, ack[:5000])
            return True

        if action == "resume":
            self._repo.set_translation_enabled(event.group_id, True)
            base_ack = decision.ack_text or "翻訳を再開します。"
            ack = self._build_multilingual_interface_message(base_ack, event.group_id)
            if event.reply_token:
                self._line.reply_text(event.reply_token, ack[:5000])
            return True

        return self._respond_unknown_instruction(event, decision.instruction_language, command_text)

    def _handle_language_settings(
        self,
        event: models.MessageEvent,
        decision: models.CommandDecision,
        command_text: Optional[str] = None,
    ) -> bool:
        op = decision.operation or "reset_all"
        valid_ops = {"reset_all", "add_and_remove", "add", "remove"}
        if op not in valid_ops:
            logger.info("Unsupported language operation", extra={"op": op})
            return self._respond_unknown_instruction(event, decision.instruction_language, command_text)
        add_langs = [(lang.code, lang.name) for lang in decision.languages_to_add]
        remove_codes = [lang.code for lang in decision.languages_to_remove]

        if op == "reset_all":
            self._repo.reset_group_language_settings(event.group_id)
            # 言語設定モード中は翻訳停止
            self._repo.set_translation_enabled(event.group_id, False)
            if event.reply_token:
                # リセット時は必ずガイダンス文言を返す（LLM 生成のあいまいな承諾メッセージを避ける）
                ack = self._translate_template(
                    "言語設定をリセットしました。通訳したい言語をすべて教えてください。",
                    decision.instruction_language,
                )
                self._line.reply_text(event.reply_token, ack[:5000])
            return True

        if op == "add_and_remove":
            if remove_codes:
                self._repo.remove_group_languages(event.group_id, remove_codes)
            if add_langs:
                self._repo.add_group_languages(event.group_id, add_langs)
        elif op == "add":
            if add_langs:
                self._repo.add_group_languages(event.group_id, add_langs)
        elif op == "remove":
            if remove_codes:
                self._repo.remove_group_languages(event.group_id, remove_codes)

        # 言語変更後は翻訳再開
        self._repo.set_translation_enabled(event.group_id, True)

        if event.reply_token:
            ack = decision.ack_text or self._translate_template("言語設定を更新しました。", decision.instruction_language)
            self._line.reply_text(event.reply_token, ack[:5000])
        return True

    def _respond_unknown_instruction(
        self,
        event: models.MessageEvent,
        instruction_lang: str,
        original_text: Optional[str] = None,
    ) -> bool:
        detected = instruction_lang or (self._lang_detector.detect(original_text) if original_text else "")
        fallback = self._build_unknown_response(detected)
        self._repo.set_translation_enabled(event.group_id, True)
        if event.reply_token and fallback:
            self._line.reply_text(event.reply_token, fallback)
        return True

    def _handle_translation_flow(self, event: models.MessageEvent, sender_name: str, translation_enabled: bool) -> bool:
        group_languages = self._repo.fetch_group_languages(event.group_id)
        candidate_languages = list(dict.fromkeys(lang for lang in group_languages if lang))

        if not candidate_languages:
            logger.info(
                "group has no language preferences yet; attempting enrollment",
                extra={"group_id": event.group_id, "user_id": event.user_id},
            )
            if self._attempt_language_enrollment(event):
                return True

        # 翻訳停止中はここで終了（メッセージは記録のみ）
        if not translation_enabled:
            return True

        context_messages = self._repo.fetch_recent_messages(event.group_id, self._max_context)
        timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        translations = self._invoke_translation_with_retry(
            sender_name=sender_name,
            message_text=event.text,
            timestamp=timestamp,
            context=context_messages,
            candidate_languages=candidate_languages,
        )
        if translations:
            reply_text = build_translation_reply(event.text, translations)
            if event.reply_token:
                self._line.reply_text(event.reply_token, reply_text)
        return True

    def _build_usage_response(self, instruction_lang: str, group_id: str) -> str:
        targets = set(self._repo.fetch_group_languages(group_id))
        if instruction_lang:
            targets.add(instruction_lang)
        targets_list = list(filter(None, targets))

        translations = self._invoke_translation_with_retry(
            sender_name="System",
            message_text=USAGE_MESSAGE_JA,
            timestamp=datetime.now(timezone.utc),
            context=[],
            candidate_languages=targets_list,
        )

        targets_lower = {lang.lower() for lang in targets_list}

        # グループまたは依頼言語に日本語が含まれる場合は、必ず日本語原文を先頭に置く
        lines: List[str] = []
        if "ja" in targets_lower:
            lines.append(USAGE_MESSAGE_JA)

        seen_langs = set()
        for item in translations:
            if item.lang.lower() in seen_langs:
                continue
            seen_langs.add(item.lang.lower())
            cleaned = strip_source_echo(USAGE_MESSAGE_JA, item.text)
            lines.append(cleaned)

        return "\n\n".join(lines)[:MAX_REPLY_LENGTH]

    def _build_unknown_response(self, instruction_lang: str) -> str:
        translations = self._invoke_translation_with_retry(
            sender_name="System",
            message_text=UNKNOWN_INSTRUCTION_JA,
            timestamp=datetime.now(timezone.utc),
            context=[],
            candidate_languages=[instruction_lang] if instruction_lang else [],
        )
        if not translations:
            return UNKNOWN_INSTRUCTION_JA
        text = strip_source_echo(UNKNOWN_INSTRUCTION_JA, translations[0].text)
        return text or UNKNOWN_INSTRUCTION_JA

    def _translate_template(self, base_text: str, instruction_lang: str) -> str:
        if not instruction_lang or instruction_lang.lower().startswith("ja"):
            return base_text
        translations = self._invoke_translation_with_retry(
            sender_name="System",
            message_text=base_text,
            timestamp=datetime.now(timezone.utc),
            context=[],
            candidate_languages=[instruction_lang],
        )
        if translations:
            return strip_source_echo(base_text, translations[0].text)
        return base_text

    def _record_message(self, event: models.MessageEvent, sender_name: str, timestamp: Optional[datetime] = None) -> None:
        if not event.group_id or not event.user_id:
            return
        ts = timestamp or datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        record = models.StoredMessage(
            group_id=event.group_id,
            user_id=event.user_id,
            sender_name=sender_name,
            text=event.text,
            timestamp=ts,
        )
        self._repo.insert_message(record)

    def _invoke_translation_with_retry(
        self,
        sender_name: str,
        message_text: str,
        timestamp: datetime,
        context: List[models.ContextMessage],
        candidate_languages: Sequence[str],
    ):
        if not candidate_languages:
            return []

        last_error: Exception | None = None
        for attempt in range(self._translation_retry):
            try:
                return self._translation.translate(
                    sender_name=sender_name,
                    message_text=message_text,
                    timestamp=timestamp,
                    context_messages=context,
                    candidate_languages=candidate_languages,
                )
            except Exception as exc:  # pylint: disable=broad-except
                if isinstance(exc, GeminiRateLimitError):
                    last_error = exc
                    break
                logger.warning(
                    "Gemini translation failed (attempt %s/%s)",
                    attempt + 1,
                    self._translation_retry,
                )
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        logger.error("Gemini translation failed after retries")
        if last_error:
            raise last_error
        return []

    def _invoke_interface_translation_with_retry(
        self,
        base_text: str,
        target_languages: Sequence[str],
    ):
        if not target_languages:
            return []

        last_error: Exception | None = None
        for attempt in range(self._translation_retry):
            try:
                return self._interface_translation.translate(base_text, target_languages)
            except Exception as exc:  # pylint: disable=broad-except
                if isinstance(exc, GeminiRateLimitError):
                    last_error = exc
                    break
                logger.warning(
                    "Gemini interface translation failed (attempt %s/%s)",
                    attempt + 1,
                    self._translation_retry,
                )
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        logger.error("Gemini interface translation failed after retries")
        if last_error:
            raise last_error
        return []

    def _build_multilingual_interface_message(self, base_text: str, group_id: str) -> str:
        languages = self._repo.fetch_group_languages(group_id)
        translations = self._invoke_interface_translation_with_retry(base_text, languages)

        if not translations:
            return base_text

        ordered_langs: List[str] = []
        seen_order = set()
        for lang in languages:
            if not lang:
                continue
            lowered = lang.lower()
            if lowered in seen_order:
                continue
            seen_order.add(lowered)
            ordered_langs.append(lang)

        text_by_lang = {}
        for item in translations:
            lowered = item.lang.lower()
            if lowered in text_by_lang:
                continue
            cleaned = strip_source_echo(base_text, item.text)
            text_by_lang[lowered] = cleaned or item.text or base_text

        lines: List[str] = []
        for lang in ordered_langs:
            lowered = lang.lower()
            text = text_by_lang.get(lowered, base_text)
            text = (text or base_text).strip()
            lines.append(f"[{lang}] {text}")

        return "\n\n".join(lines)[:MAX_REPLY_LENGTH]

    def _send_rate_limit_notice(self, event: models.MessageEvent) -> None:
        key = event.group_id or event.user_id or "unknown"
        if _last_rate_limit_message.get(key) == RATE_LIMIT_MESSAGE:
            return
        if event.reply_token:
            self._line.reply_text(event.reply_token, RATE_LIMIT_MESSAGE)
            _last_rate_limit_message[key] = RATE_LIMIT_MESSAGE

    def _prepare_language_prompt_texts(self, supported, preference: models.LanguagePreference) -> Dict[str, str]:
        primary_lang = (preference.primary_language or "").lower()

        base_confirm = self._build_simple_confirm_text(supported)
        confirm_text = preference.confirm_text or self._translate_template(base_confirm, primary_lang)
        if not confirm_text:
            confirm_text = base_confirm
        confirm_text = self._truncate(confirm_text, 240)

        base_completion = _build_completion_message([(lang.code, lang.name) for lang in supported])
        completion_text = preference.completion_text or self._translate_template(base_completion, primary_lang)
        if not completion_text:
            completion_text = base_completion
        completion_text = self._truncate(completion_text, 240)

        base_cancel = _build_cancel_message()
        cancel_text = preference.cancel_text or self._translate_template(base_cancel, primary_lang)
        if not cancel_text:
            cancel_text = base_cancel
        cancel_text = self._truncate(cancel_text, 240)

        return {
            "primary_language": primary_lang,
            "confirm_text": confirm_text,
            "completion_text": completion_text,
            "cancel_text": cancel_text,
        }

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _resolve_sender_name(self, event: models.MessageEvent) -> str:
        if event.user_id:
            name = self._line.get_display_name(event.sender_type, event.group_id, event.user_id)
            if name:
                return name
        return event.user_id or "Unknown"

    @staticmethod
    def _format_unsupported_message(languages) -> str:
        messages = []
        for lang in languages:
            primary = lang.name or lang.code
            english = lang.code
            thai = lang.code
            messages.append(
                f"{primary}には通訳対応できません。\n"
                f"I cannot provide interpretation for {english}.\n"
                f"ฉันไม่สามารถให้บริการล่ามสำหรับ{thai}ได้"
            )
        return "\n\n".join(messages)

    @staticmethod
    def _build_simple_confirm_text(languages) -> str:
        names = [lang.name or lang.code for lang in languages]
        joined = "、".join(filter(None, names))
        if joined:
            return f"{joined}の翻訳を有効にしますか？"
        return "翻訳したい言語を確認してもよろしいですか？"

    @staticmethod
    def _encode_postback_payload(payload: Dict, max_bytes: int = 320) -> str:
        """Encode payload for LINE postback with size guard (LINE上限≈300 bytes)."""
        def _encode(data: Dict) -> str:
            raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
            compressed = base64.urlsafe_b64encode(zlib.compress(raw)).decode("ascii").rstrip("=")
            return f"langpref2={compressed}"

        encoded = _encode(payload)
        if len(encoded.encode("utf-8")) <= max_bytes:
            return encoded

        # try shortening optional texts first
        def _shrink_text(key: str, factor: float = 0.6) -> bool:
            if key in payload and payload[key]:
                text = payload[key]
                new_len = max(int(len(text) * factor), 40)
                payload[key] = text[:new_len]
                return True
            return False

        for _ in range(3):
            changed = False
            for key in ("completion_text", "cancel_text"):
                changed |= _shrink_text(key)
            encoded = _encode(payload)
            if len(encoded.encode("utf-8")) <= max_bytes:
                return encoded
            if not changed:
                break

        # last resort: drop optional texts
        payload.pop("completion_text", None)
        payload.pop("cancel_text", None)
        encoded = _encode(payload)
        return encoded[:max_bytes]
