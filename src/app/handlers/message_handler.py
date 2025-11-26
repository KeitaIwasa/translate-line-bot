from __future__ import annotations

import base64
import json
import logging
import time
import zlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from ...domain import models
from ...domain.ports import LanguagePreferencePort, LinePort, MessageRepositoryPort
from ...domain.services.translation_service import TranslationService
from ...infra.gemini_translation import GeminiRateLimitError
from ...presentation.reply_formatter import build_translation_reply

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = "You have reached the rate limit. Please try again later."
_last_rate_limit_message: Dict[str, str] = {}

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
        language_pref_service: LanguagePreferencePort,
        repo: MessageRepositoryPort,
        max_context_messages: int,
        translation_retry: int,
    ) -> None:
        self._line = line_client
        self._translation = translation_service
        self._lang_pref = language_pref_service
        self._repo = repo
        self._max_context = max_context_messages
        self._translation_retry = translation_retry

    def handle(self, event: models.MessageEvent) -> None:
        if not event.reply_token:
            return

        # 1: 個チャットではグループ招待を案内
        if event.sender_type == "user" and (not event.group_id or event.group_id == event.user_id):
            self._line.reply_text(event.reply_token, DIRECT_GREETING)
            return

        if not event.group_id or not event.user_id:
            return

        self._repo.ensure_group_member(event.group_id, event.user_id)

        timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
        sender_name = self._resolve_sender_name(event)

        group_languages = self._repo.fetch_group_languages(event.group_id)
        candidate_languages = list(dict.fromkeys(lang for lang in group_languages if lang))

        if not candidate_languages:
            logger.info(
                "group has no language preferences yet; attempting enrollment",
                extra={"group_id": event.group_id, "user_id": event.user_id},
            )
            if self._attempt_language_enrollment(event):
                return

        context_messages = self._repo.fetch_recent_messages(event.group_id, self._max_context)

        record = models.StoredMessage(
            group_id=event.group_id,
            user_id=event.user_id,
            sender_name=sender_name,
            text=event.text,
            timestamp=timestamp,
        )
        try:
            translations = self._invoke_translation_with_retry(
                sender_name=sender_name,
                message_text=event.text,
                timestamp=timestamp,
                context=context_messages,
                candidate_languages=candidate_languages,
            )
            if translations:
                reply_text = build_translation_reply(event.text, translations)
                self._line.reply_text(event.reply_token, reply_text)
        except GeminiRateLimitError:
            logger.warning("Gemini rate limited; notifying user")
            self._send_rate_limit_notice(event)
        except Exception:
            logger.exception("Translation pipeline failed")
        finally:
            try:
                self._repo.insert_message(record)
            except Exception:
                logger.exception("Failed to persist message")

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

        confirm_payload = self._encode_postback_payload(
            {
                "kind": "language_confirm",
                "action": "confirm",
                "languages": [{"code": lang.code, "name": lang.name} for lang in supported],
            }
        )
        cancel_payload = self._encode_postback_payload(
            {"kind": "language_confirm", "action": "cancel"}
        )

        confirm_text = self._build_simple_confirm_text(supported)[:400]
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
        logger.info(
            "Language enrollment prompt sent",
            extra={"group_id": event.group_id, "user_id": event.user_id, "prompted_langs": [lang.code for lang in supported]},
        )
        return True

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

    def _send_rate_limit_notice(self, event: models.MessageEvent) -> None:
        key = event.group_id or event.user_id or "unknown"
        if _last_rate_limit_message.get(key) == RATE_LIMIT_MESSAGE:
            return
        if event.reply_token:
            self._line.reply_text(event.reply_token, RATE_LIMIT_MESSAGE)
            _last_rate_limit_message[key] = RATE_LIMIT_MESSAGE

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
    def _encode_postback_payload(payload: Dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        compressed = base64.urlsafe_b64encode(zlib.compress(raw)).decode("ascii").rstrip("=")
        return f"langpref2={compressed}"
