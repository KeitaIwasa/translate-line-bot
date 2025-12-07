from __future__ import annotations

import base64
import json
import logging
import re
import time
from functools import partial
import zlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from concurrent.futures import ThreadPoolExecutor, Future

import requests

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
LANGUAGE_LIMIT_MESSAGE_EN = "You can set up to {limit} translation languages. Please specify {limit} or fewer."


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
        max_group_languages: int,
        translation_retry: int,
        bot_mention_name: str,
        stripe_secret_key: str = "",
        stripe_price_monthly_id: str = "",
        free_quota_per_month: int = 50,
        pro_quota_per_month: int = 8000,
        checkout_base_url: str = "",
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._line = line_client
        self._translation = translation_service
        self._interface_translation = interface_translation
        self._lang_detector = language_detector
        self._lang_pref = language_pref_service
        self._command_router = command_router
        self._repo = repo
        self._max_context = max_context_messages
        self._max_group_languages = max_group_languages
        self._translation_retry = translation_retry
        self._bot_mention_name = bot_mention_name
        self._stripe_secret_key = stripe_secret_key
        self._stripe_price_monthly_id = stripe_price_monthly_id
        self._free_quota = free_quota_per_month
        self._pro_quota = pro_quota_per_month
        self._checkout_base_url = checkout_base_url.rstrip("/") if checkout_base_url else ""
        self._executor = executor or ThreadPoolExecutor(max_workers=4)

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
            handled = self._process_group_message(event, sender_name)
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
        
    def _process_group_message(self, event: models.MessageEvent, sender_name: str) -> bool:
        """グループ向けメッセージのディスパッチを担当。"""
        command_text = self._extract_command_text(event.text)
        if command_text:
            return self._handle_command(event, command_text)

        return self._handle_translation_flow(
            event,
            sender_name,
            translation_enabled=self._repo.is_translation_enabled(event.group_id),
        )

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

        detected_total = len(supported) + len(unsupported)
        if detected_total > self._max_group_languages:
            logger.info(
                "Language selection exceeds max allowed (by detected count)",
                extra={
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                    "detected_total": detected_total,
                    "max": self._max_group_languages,
                },
            )
            message = self._build_language_limit_message(result.primary_language)
            if event.reply_token:
                self._line.reply_text(event.reply_token, message[:5000])
            # 翻訳は停止したままにする
            self._repo.set_translation_enabled(event.group_id, False)
            return True

        messages: List[Dict] = []
        limited_supported, dropped = self._limit_language_choices(supported)
        if unsupported:
            messages.append(
                {
                    "type": "text",
                    "text": self._format_unsupported_message(unsupported, result.primary_language),
                }
            )
        if dropped:
            notice = self._build_language_limit_message(result.primary_language)
            messages.append({"type": "text", "text": notice})
            if event.reply_token:
                self._line.reply_messages(event.reply_token, messages)
            # 翻訳を一時停止し、再指定を促す
            self._repo.set_translation_enabled(event.group_id, False)
            return True

        # 対応言語がなければ未対応メッセージだけ返して終了
        if not limited_supported:
            if messages and event.reply_token:
                self._line.reply_messages(event.reply_token, messages)
            return True

        prompt_texts = self._prepare_language_prompt_texts(limited_supported, result)
        confirm_payload = self._encode_postback_payload(
            {
                "kind": "language_confirm",
                "action": "confirm",
                "languages": [{"code": lang.code, "name": lang.name} for lang in limited_supported],
                "primary_language": prompt_texts["primary_language"],
                "completion_text": prompt_texts["completion_text"],
                "limit_text": self._build_language_limit_message(result.primary_language),
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
                    {"type": "postback", "label": f"🆗 {prompt_texts['confirm_label']}", "data": confirm_payload},
                    {"type": "postback", "label": f"↩️ {prompt_texts['cancel_label']}", "data": cancel_payload},
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
        lang_future: Future[List[str]] | None = None
        instr_future: Future[str] | None = None
        try:
            lang_future = self._executor.submit(self._fetch_and_limit_languages, event.group_id)
            instr_future = self._executor.submit(self._lang_detector.detect, command_text)
        except Exception:
            logger.debug("Executor submission failed; fallback to sync", exc_info=True)

        decision = self._command_router.decide(command_text)
        action = decision.action or "unknown"
        instruction_lang = decision.instruction_language
        if not instruction_lang and instr_future:
            try:
                instruction_lang = instr_future.result()
            except Exception:
                logger.debug("Instruction language detect failed", exc_info=True)
                instruction_lang = decision.instruction_language

        if action == "language_settings":
            return self._handle_language_settings(event, decision, command_text)

        if action == "howto":
            langs: Optional[List[str]] = None
            if lang_future:
                try:
                    langs = lang_future.result()
                except Exception:
                    logger.debug("Language fetch future failed", exc_info=True)
            message = self._build_usage_response(instruction_lang or decision.instruction_language, event.group_id, precomputed_languages=langs)
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

        return self._respond_unknown_instruction(event, instruction_lang or decision.instruction_language, command_text)

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

        current_langs = self._dedup_language_codes(self._repo.fetch_group_languages(event.group_id))
        if op in {"add", "add_and_remove"}:
            if self._would_exceed_language_limit(current_langs, add_langs, remove_codes):
                logger.info(
                    "Language update rejected: exceeds max",
                    extra={
                        "group_id": event.group_id,
                        "current": current_langs,
                        "add": [code for code, _ in add_langs],
                        "remove": remove_codes,
                    },
                )
                if event.reply_token:
                    msg = self._build_language_limit_message(decision.instruction_language)
                    self._line.reply_text(event.reply_token, msg[:5000])
                return True

        if op == "reset_all":
            self._repo.reset_group_language_settings(event.group_id)
            # 言語設定モード中は翻訳停止
            self._repo.set_translation_enabled(event.group_id, False)
            if event.reply_token:
                # リセット時は必ずガイダンス文言を返す（LLM 生成のあいまいな承諾メッセージを避ける）
                ack = self._translate_template(
                    "Your language settings have been reset. Please tell us all the languages ​​you would like to translate.",
                    decision.instruction_language,
                )
                self._line.reply_text(event.reply_token, ack[:5000])
            return True

        if op == "add_and_remove":
            remove_set = {code.lower() for code in remove_codes if code}
            after_remove = [lang for lang in current_langs if lang not in remove_set]
            normalized_add = self._normalize_new_languages(add_langs, set(after_remove))
            if remove_codes:
                self._repo.remove_group_languages(event.group_id, remove_codes)
            if normalized_add:
                self._repo.add_group_languages(event.group_id, normalized_add)
        elif op == "add":
            normalized_add = self._normalize_new_languages(add_langs, set(current_langs))
            if normalized_add:
                self._repo.add_group_languages(event.group_id, normalized_add)
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
        candidate_languages = self._limit_language_codes(group_languages)

        if not candidate_languages:
            logger.info(
                "group has no language preferences yet; attempting enrollment",
                extra={"group_id": event.group_id, "user_id": event.user_id},
            )
            if self._attempt_language_enrollment(event):
                return True

        if not translation_enabled:
            # 停止理由に応じた案内を返して終了
            self._send_pause_notice(event)
            return True

        paid = self._is_subscription_active(event.group_id)
        limit = self._pro_quota if paid else self._free_quota
        month_key = self._current_month_key()

        # すでに上限到達済みの場合はカウントを進めずに終了
        current_usage = self._repo.get_usage(event.group_id, month_key)
        if current_usage >= limit:
            if not paid:
                self._repo.set_translation_enabled(event.group_id, False)
            self._send_over_quota_message(event, paid, limit)
            return True

        # 利用カウントと課金判定
        usage_count = self._repo.increment_usage(event.group_id, month_key)

        if usage_count > limit:
            if not paid:
                self._repo.set_translation_enabled(event.group_id, False)
            self._send_over_quota_message(event, paid, limit)
            return True
        if usage_count == limit:
            self._send_quota_warning(event, paid, limit)

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

    def _send_quota_warning(self, event: models.MessageEvent, paid: bool, limit: int) -> None:
        # 上限到達（ちょうど）の通知
        if paid:
            base = (
                f"You have reached the limit of the Pro plan ({limit:,} messages per month).\n"
                "Translation will be paused from the next message in this billing cycle."
            )
            url = None
        else:
            base = (
                f"You have reached the limit of the free plan ({limit:,} messages per month).\n"
                "Starting with the next message, a paid plan is required. Please complete checkout to continue."
            )
            url = self._build_checkout_url(event.group_id)

        message = self._build_multilingual_notice(
            base,
            event.group_id,
            url,
            add_missing_link_notice=not paid,
        )
        if event.reply_token:
            self._line.reply_text(event.reply_token, message[:5000])

    def _send_over_quota_message(self, event: models.MessageEvent, paid: bool, limit: int) -> None:
        # 上限超過後の停止通知
        if paid:
            base = (
                f"The Pro plan monthly limit ({limit:,} messages) has been reached and translation is paused.\n"
                "Please wait for the next monthly cycle or contact the administrator."
            )
            url = None
        else:
            base = (
                f"Free quota ({limit:,} messages per month) is exhausted and translation is paused.\n"
                "Purchase below to resume the service."
            )
            url = self._build_checkout_url(event.group_id)

        message = self._build_multilingual_notice(
            base,
            event.group_id,
            url,
            add_missing_link_notice=not paid,
        )
        if event.reply_token:
            self._line.reply_text(event.reply_token, message[:5000])

    def _send_pause_notice(self, event: models.MessageEvent) -> None:
        """translation_enabled=False のときに理由別の案内を返す。"""
        paid = self._is_subscription_active(event.group_id)
        limit = self._pro_quota if paid else self._free_quota
        usage = self._repo.get_usage(event.group_id, self._current_month_key())

        # 上限超過が原因で停止している場合
        if usage >= limit:
            self._send_over_quota_message(event, paid, limit)
            return

        if paid:
            base = "Translation is currently paused. Please try again later or contact the administrator."
            url = None
        else:
            base = (
                "Translation is currently paused, likely because the free quota was exceeded.\n"
                "To continue, please complete the checkout below."
            )
            url = self._build_checkout_url(event.group_id)

        message = self._build_multilingual_notice(
            base,
            event.group_id,
            url,
            add_missing_link_notice=not paid,
        )
        if event.reply_token:
            self._line.reply_text(event.reply_token, message[:5000])

    def _build_multilingual_notice(
        self,
        base_text: str,
        group_id: str,
        url: Optional[str],
        *,
        add_missing_link_notice: bool = True,
    ) -> str:
        translated_block = self._build_multilingual_interface_message(base_text, group_id)
        lines = [translated_block]
        if url:
            lines.append(url)
        elif add_missing_link_notice:
            lines.append("(Unable to generate purchase link at this time, please contact administrator.)")
        return "\n\n".join(filter(None, lines))

    def _build_checkout_url(self, group_id: str) -> Optional[str]:
        import importlib

        try:
            stripe = importlib.import_module("stripe")
        except ModuleNotFoundError:
            logger.warning("stripe SDK not available; cannot create checkout session")
            return None

        secret = self._stripe_secret_key
        price_id = self._stripe_price_monthly_id
        if not secret or not price_id:
            return None

        stripe.api_key = secret
        try:
            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url="https://line.me",
                cancel_url="https://line.me",
                metadata={"group_id": group_id},
                subscription_data={"metadata": {"group_id": group_id}},
            )
            checkout_url = getattr(session, "url", None)
            session_id = getattr(session, "id", None)

            if self._checkout_base_url and session_id:
                return f"{self._checkout_base_url}/checkout?session_id={session_id}"

            return checkout_url
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to create checkout session: %s", exc)
            return None

    def _is_subscription_active(self, group_id: str) -> bool:
        status = self._repo.get_subscription_status(group_id)
        return status in {"active", "trialing"}

    @staticmethod
    def _current_month_key() -> str:
        now = datetime.now(timezone.utc)
        return f"{now.year:04d}-{now.month:02d}"

    def _build_usage_response(
        self,
        instruction_lang: str,
        group_id: str,
        precomputed_languages: Optional[List[str]] = None,
    ) -> str:
        base_targets = list(precomputed_languages or self._repo.fetch_group_languages(group_id))
        if instruction_lang:
            base_targets.append(instruction_lang)
        targets_list = self._limit_language_codes(base_targets)

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
            return self._normalize_bullet_newlines(UNKNOWN_INSTRUCTION_JA)
        text = strip_source_echo(UNKNOWN_INSTRUCTION_JA, translations[0].text)
        normalized = self._normalize_bullet_newlines(text or UNKNOWN_INSTRUCTION_JA)
        return normalized

    def _normalize_bullet_newlines(self, text: str) -> str:
        """箇条書きのハイフンの前に改行を強制して読みやすくする。"""
        return re.sub(r"(?<!\n)(- )", "\n- ", text)

    def _build_language_limit_message(self, instruction_lang: str) -> str:
        base = LANGUAGE_LIMIT_MESSAGE_EN.format(limit=self._max_group_languages)
        if not instruction_lang or instruction_lang.lower().startswith("en"):
            return base

        manual = None
        lowered = instruction_lang.lower()
        translated = self._translate_template(base, instruction_lang, force=True)
        if translated and translated != base:
            return translated
        return manual or translated or base

    def _translate_template(
        self,
        base_text: str | Sequence[str],
        instruction_lang: str,
        *,
        force: bool = False,
    ) -> str | List[str]:
        if isinstance(base_text, str):
            originals = [base_text]
            is_sequence = False
        else:
            originals = list(base_text)
            is_sequence = True

        if not instruction_lang:
            return base_text

        lowered = instruction_lang.lower()
        if lowered.startswith("en") and not force:
            return base_text

        if not originals:
            return base_text

        delimiter = "\n---\n"
        joined = delimiter.join(originals)

        translations = self._invoke_translation_with_retry(
            sender_name="System",
            message_text=joined,
            timestamp=datetime.now(timezone.utc),
            context=[],
            candidate_languages=[instruction_lang],
        )
        if not translations:
            return base_text

        translated = strip_source_echo(joined, translations[0].text) or translations[0].text or joined
        parts = translated.split(delimiter)
        if len(parts) != len(originals):
            return base_text

        normalized = [self._normalize_template_text(part or orig) for part, orig in zip(parts, originals)]
        if is_sequence:
            return normalized
        return normalized[0]

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

        return self._run_with_retry(
            label="Gemini translation",
            func=partial(
                self._translation.translate,
                sender_name=sender_name,
                message_text=message_text,
                timestamp=timestamp,
                context_messages=context,
                candidate_languages=candidate_languages,
            ),
            timeout_seconds=getattr(getattr(self._translation, "_translator", None), "_timeout", None),
        )

    def _invoke_interface_translation_with_retry(
        self,
        base_text: str,
        target_languages: Sequence[str],
    ):
        if not target_languages:
            return []

        return self._run_with_retry(
            label="Gemini interface translation",
            func=partial(self._interface_translation.translate, base_text, target_languages),
            timeout_seconds=getattr(getattr(self._interface_translation, "_translator", None), "_timeout", None),
        )

    def _run_with_retry(self, label: str, func, timeout_seconds: int | None = None):
        """翻訳系リトライ共通処理。"""
        last_error: Exception | None = None
        for attempt in range(self._translation_retry):
            try:
                return func()
            except requests.exceptions.Timeout as exc:
                logger.warning(
                    "%s timeout",
                    label,
                    extra={"attempt": attempt + 1, "timeout_seconds": timeout_seconds},
                )
                last_error = exc
            except Exception as exc:  # pylint: disable=broad-except
                if isinstance(exc, GeminiRateLimitError):
                    last_error = exc
                    break
                logger.warning(
                    "%s failed (attempt %s/%s)",
                    label,
                    attempt + 1,
                    self._translation_retry,
                )
                last_error = exc
            time.sleep(0.5 * (attempt + 1))

        logger.error("%s failed after retries", label)
        if last_error:
            raise last_error
        return []

    def _build_multilingual_interface_message(self, base_text: str, group_id: str) -> str:
        languages = self._limit_language_codes(self._repo.fetch_group_languages(group_id))
        translations = self._invoke_interface_translation_with_retry(base_text, languages)

        if not translations:
            return base_text

        text_by_lang = {}
        for item in translations:
            lowered = item.lang.lower()
            if lowered in text_by_lang:
                continue
            cleaned = strip_source_echo(base_text, item.text)
            text_by_lang[lowered] = cleaned or item.text or base_text

        lines: List[str] = []
        for lang in languages:
            lowered = lang.lower()
            text = text_by_lang.get(lowered, base_text)
            text = (text or base_text).strip()
            lines.append(text)

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
        base_cancel = _build_cancel_message()
        base_confirm_label = preference.confirm_label or "OK"
        base_cancel_label = preference.cancel_label or "Cancel"

        # 1リクエストで confirm/cancel 文言とボタンラベルをまとめて翻訳
        translated = self._translate_template(
            [base_confirm, base_cancel, base_confirm_label, base_cancel_label],
            primary_lang,
            force=True,
        )
        (
            translated_confirm,
            translated_cancel,
            translated_confirm_label,
            translated_cancel_label,
        ) = translated if isinstance(translated, list) else [base_confirm, base_cancel, base_confirm_label, base_cancel_label]

        confirm_text = self._normalize_template_text(translated_confirm or base_confirm)
        confirm_text = self._truncate(confirm_text or base_confirm, 240)

        base_completion = _build_completion_message([(lang.code, lang.name) for lang in supported])
        completion_text = self._normalize_template_text(base_completion)
        completion_text = self._truncate(completion_text or base_completion, 240)

        cancel_text = self._normalize_template_text(translated_cancel or base_cancel)
        cancel_text = self._truncate(cancel_text or base_cancel, 240)

        confirm_label = self._truncate(translated_confirm_label or base_confirm_label, 16)
        cancel_label = self._truncate(translated_cancel_label or base_cancel_label, 16)

        return {
            "primary_language": primary_lang,
            "confirm_text": confirm_text,
            "completion_text": completion_text,
            "cancel_text": cancel_text,
            "confirm_label": confirm_label,
            "cancel_label": cancel_label,
        }

    @staticmethod
    def _normalize_template_text(text: str) -> str:
        """軽微な生成ゆらぎで先頭に挿入される余白を除去し、空行を詰める。"""
        if not text:
            return ""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized

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

    def _format_unsupported_message(self, languages, instruction_lang: Optional[str] = None) -> str:
        base_messages = []
        for lang in languages:
            name = lang.name or lang.code
            base_messages.append(f"I cannot provide interpretation for {name}.")

        combined = "\n\n".join(base_messages)
        if not instruction_lang or instruction_lang.lower().startswith("en"):
            return combined

        translated = self._translate_template(combined, instruction_lang, force=True)
        normalized = self._normalize_template_text(translated or combined)
        return self._truncate(normalized or combined, 5000)

    @staticmethod
    def _build_simple_confirm_text(languages) -> str:
        names = [lang.name or lang.code for lang in languages]
        filtered = [name for name in names if name]
        if not filtered:
            return "Do you want to enable translation?"
        if len(filtered) == 1:
            joined = filtered[0]
        elif len(filtered) == 2:
            joined = " and ".join(filtered)
        else:
            joined = ", ".join(filtered[:-1]) + ", and " + filtered[-1]
        return f"Do you want to enable translation for {joined}?"

    def _fetch_and_limit_languages(self, group_id: str) -> List[str]:
        return self._limit_language_codes(self._repo.fetch_group_languages(group_id))

    def _would_exceed_language_limit(
        self,
        current_langs: Sequence[str],
        add_langs: Sequence[Tuple[str, str]],
        remove_codes: Sequence[str],
    ) -> bool:
        remove_set = {code.lower() for code in remove_codes if code}
        remaining = [code.lower() for code in current_langs if code and code.lower() not in remove_set]

        to_add: list[str] = []
        seen = set(remaining)
        for code, _name in add_langs:
            if not code:
                continue
            lowered = code.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            to_add.append(lowered)

        final_count = len(remaining) + len(to_add)
        return final_count > self._max_group_languages

    def _limit_language_choices(self, languages: Sequence[models.LanguageChoice]) -> Tuple[List[models.LanguageChoice], List[models.LanguageChoice]]:
        limited: List[models.LanguageChoice] = []
        dropped: List[models.LanguageChoice] = []
        seen = set()
        for lang in languages:
            code = (lang.code or "").lower()
            if not code or code in seen:
                continue
            seen.add(code)
            if len(limited) < self._max_group_languages:
                limited.append(models.LanguageChoice(code=code, name=lang.name))
            else:
                dropped.append(models.LanguageChoice(code=code, name=lang.name))
        return limited, dropped

    def _dedup_language_codes(self, languages: Sequence[str]) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for code in languages:
            lowered = (code or "").lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(lowered)
        return deduped

    def _limit_language_codes(self, languages: Sequence[str]) -> List[str]:
        deduped = self._dedup_language_codes(languages)
        return deduped[: self._max_group_languages]

    def _normalize_new_languages(self, languages: Sequence[Tuple[str, str]], existing_set: set[str]) -> List[Tuple[str, str]]:
        normalized: List[Tuple[str, str]] = []
        seen = set(existing_set)
        for code, name in languages:
            lowered = (code or "").lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            normalized.append((lowered, name))
        return normalized

    @staticmethod
    def _encode_postback_payload(payload: Dict, max_bytes: int = 280) -> str:
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
                new_len = max(int(len(text) * factor), 32)
                payload[key] = text[:new_len]
                return True
            return False

        optional_keys = ("limit_text", "cancel_text", "completion_text")
        for key in optional_keys:
            # try shrinking this key up to 3 times before moving on
            for _ in range(3):
                changed = _shrink_text(key)
                encoded = _encode(payload)
                if len(encoded.encode("utf-8")) <= max_bytes:
                    return encoded
                if not changed:
                    break
            # drop the key entirely if still too large
            if key in payload:
                payload.pop(key, None)
                encoded = _encode(payload)
                if len(encoded.encode("utf-8")) <= max_bytes:
                    return encoded

        encoded = _encode(payload)
        return encoded[:max_bytes]
