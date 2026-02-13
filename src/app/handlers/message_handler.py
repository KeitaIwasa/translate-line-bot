from __future__ import annotations

import logging
import json
import base64
import zlib
import re
import time
from functools import partial
from datetime import datetime, timezone
from calendar import monthrange
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
    _wrap_bidi_isolate,
    strip_source_echo,
)
from ..subscription_texts import (
    SUBS_CANCEL_LABEL,
    SUBS_ALREADY_PRO_TEXT,
    SUBS_CANCEL_CONFIRM_TEXT,
    SUBS_MENU_TEXT,
    SUBS_MENU_TITLE,
    SUBS_NOT_PRO_TEXT,
    SUBS_UPGRADE_LABEL,
    SUBS_UPGRADE_LINK_FAIL,
    SUBS_VIEW_LABEL,
)
from ..subscription_templates import (
    build_subscription_cancel_confirm,
    build_subscription_menu_message,
)
from ...domain.services.subscription_service import SubscriptionService
from ...domain.services.quota_service import QuotaService
from ...domain.services.translation_flow_service import TranslationFlowService
from ...domain.services.language_settings_service import LanguageSettingsService
from ...domain.services.private_chat_support_service import PrivateChatSupportService
from ...domain.services.plan_policy import (
    FREE_PLAN,
    PRO_PLAN,
    STANDARD_PLAN,
    language_limit_for,
    monthly_quota_for,
    normalize_plan_key,
    resolve_effective_plan,
    stop_translation_on_quota,
)
from .postback_handler import _build_cancel_message, _build_completion_message

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = "You have reached the rate limit. Please try again later."
_last_rate_limit_message: Dict[str, str] = {}

# 利用方法案内文言
USAGE_MESSAGE = (
    "After setting your language preferences, feel free to chat in any language. "
    "This bot will deliver translations to each selected language every time you post."
)

# メンション機能一覧の案内文言
UNKNOWN_INSTRUCTION_BASE = (
    "To interact with this bot, please mention it again and provide one of the following commands:\n"
    "- Change language settings\n- How to use\n- Stop translation\n- Subscription management"
)

GROUP_PROMPT_MESSAGE = (
    "I'm a multilingual translation bot. Please tell me the languages you want to translate to.\n\n"
    "多言語翻訳ボットです。翻訳したい言語を教えてください。\n\n"
    "我是一个多语言翻译机器人。请告诉我你想要翻译成哪些语言。\n\n"
    "ฉันเป็นบอทแปลหลายภาษา กรุณาบอกฉันว่าคุณต้องการแปลเป็นภาษาใดบ้าง\n\n"
    "ex) English, 中文, 日本語, ไทย"
)
LANGUAGE_ANALYSIS_FALLBACK = (
    "ごめんなさい、翻訳する言語の確認に失敗しました。数秒おいてから、翻訳したい言語をカンマ区切りで送ってください。\n"
    "Sorry, I couldn't detect your languages. Please resend after a few seconds (e.g., English, 日本語, 中文, ไทย).\n"
    "ขออภัย ไม่สามารถระบุภาษาได้ กรุณาลองส่งมาใหม่อีกครั้ง (ตัวอย่าง: English, 日本語, 中文, ไทย)"
)
LANGUAGE_LIMIT_MESSAGE_EN = "You can set up to {limit} translation languages. Please specify {limit} or fewer."
PRIVATE_ASSISTANT_USER_ID = "__assistant__"
PRIVATE_ASSISTANT_SENDER = "KOTORI Support"


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
        standard_quota_per_month: int = 4000,
        pro_quota_per_month: int = 40000,
        subscription_frontend_base_url: str = "",
        checkout_api_base_url: str = "",
        subscription_service: SubscriptionService | None = None,
        executor: ThreadPoolExecutor | None = None,
        quota_service: QuotaService | None = None,
        translation_flow_service: TranslationFlowService | None = None,
        language_settings_service: LanguageSettingsService | None = None,
        private_chat_support_service: PrivateChatSupportService | None = None,
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
        self._standard_quota = standard_quota_per_month
        self._pro_quota = pro_quota_per_month
        # 案内ポータル (GitHub Pages 等) のベース URL
        self._subscription_frontend_base_url = subscription_frontend_base_url.rstrip("/") if subscription_frontend_base_url else ""
        self._subscription_service = subscription_service or SubscriptionService(
            repo,
            stripe_secret_key,
            stripe_price_monthly_id,
            subscription_frontend_base_url,
            checkout_api_base_url,
        )
        self._quota = quota_service or QuotaService(repo)
        self._translation_flow = translation_flow_service or TranslationFlowService(
            repo,
            translation_service,
            interface_translation,
            self._quota,
            max_context_messages=max_context_messages,
            translation_retry=translation_retry,
        )
        self._language_settings = language_settings_service or LanguageSettingsService(
            repo,
            language_pref_service,
            interface_translation,
            max_group_languages,
        )
        self._private_chat_support = private_chat_support_service
        self._executor = executor or ThreadPoolExecutor(max_workers=4)

    def handle(self, event: models.MessageEvent) -> None:
        if not event.reply_token:
            return

        if not event.group_id or not event.user_id:
            return

        timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        # 個人チャットはサポート応答を返す。
        if event.sender_type == "user" and event.group_id == event.user_id:
            sender_name, _deferred_name = self._resolve_sender_name(event)
            self._handle_private_chat(event, sender_name, timestamp)
            return

        self._repo.ensure_group_member(event.group_id, event.user_id)
        sender_name, deferred_name = self._resolve_sender_name(event)

        logger.info(
            "Handling message event | group=%s user=%s sender=%s text=%.40s",
            event.group_id,
            event.user_id,
            event.sender_type,
            (event.text or ""),
        )

        handled = False
        try:
            handled = self._process_group_message(event, sender_name, deferred_name)
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
        
    def _process_group_message(
        self,
        event: models.MessageEvent,
        sender_name: str,
        deferred_display_name: str | None,
    ) -> bool:
        """グループ向けメッセージのディスパッチを担当。"""
        command_text = self._extract_command_text(event.text)
        # メンションさえ含まれていればコマンド扱い（空文字でも許可）
        if command_text is not None:
            return self._handle_command(event, command_text)

        return self._handle_translation_flow(event, sender_name, deferred_display_name=deferred_display_name)

    def _handle_private_chat(
        self,
        event: models.MessageEvent,
        sender_name: str,
        timestamp: datetime,
    ) -> None:
        if not self._private_chat_support:
            logger.warning("Private chat support is not configured")
            return

        response = self._private_chat_support.respond(event.user_id or "", event.text or "")

        try:
            self._repo.insert_message(
                models.StoredMessage(
                    group_id=event.group_id or "",
                    user_id=event.user_id or "",
                    sender_name=sender_name,
                    text=response.safe_input_text or event.text,
                    timestamp=timestamp,
                    message_role="user",
                )
            )
        except Exception:
            logger.exception("Failed to persist direct user message")

        if event.reply_token and response.output_text:
            try:
                self._line.reply_text(event.reply_token, response.output_text[:5000])
            except Exception:
                logger.exception("Failed to reply direct message")

        try:
            self._repo.insert_message(
                models.StoredMessage(
                    group_id=event.group_id or "",
                    user_id=PRIVATE_ASSISTANT_USER_ID,
                    sender_name=PRIVATE_ASSISTANT_SENDER,
                    text=response.safe_output_text or response.output_text,
                    timestamp=datetime.now(timezone.utc),
                    message_role="assistant",
                )
            )
        except Exception:
            logger.exception("Failed to persist direct assistant message")

    # --- internal helpers ---
    def _attempt_language_enrollment(self, event: models.MessageEvent) -> bool:
        plan_key = self._resolve_effective_plan_for_group(event.group_id)
        bundle = self._language_settings.propose(
            event,
            max_languages=language_limit_for(plan_key),
        )
        if not bundle:
            return False
        if event.reply_token:
            if bundle.messages:
                self._line.reply_messages(event.reply_token, list(bundle.messages))
            elif bundle.texts:
                self._line.reply_text(event.reply_token, bundle.texts[0])
        logger.info(
            "Language enrollment prompt sent",
            extra={"group_id": event.group_id, "user_id": event.user_id},
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
            base_ack = "I will pause translation. Please mention me again when you want to resume."
            ack = self._build_multilingual_interface_message(base_ack, event.group_id)
            if event.reply_token:
                self._line.reply_text(event.reply_token, ack[:5000])
            return True

        if action == "resume":
            self._repo.set_translation_enabled(event.group_id, True)
            base_ack = "I will resume the translation."
            ack = self._build_multilingual_interface_message(base_ack, event.group_id)
            if event.reply_token:
                self._line.reply_text(event.reply_token, ack[:5000])
            return True

        if action == "subscription_menu":
            return self._handle_subscription_menu(event, instruction_lang or decision.instruction_language)

        if action == "subscription_cancel":
            return self._handle_subscription_cancel(event, instruction_lang or decision.instruction_language)

        if action == "subscription_upgrade":
            return self._handle_subscription_upgrade(event, instruction_lang or decision.instruction_language)

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
        plan_key = self._resolve_effective_plan_for_group(event.group_id)
        language_limit = language_limit_for(plan_key)

        current_langs = self._dedup_language_codes(self._repo.fetch_group_languages(event.group_id))
        if op in {"add", "add_and_remove"}:
            if self._would_exceed_language_limit(
                current_langs,
                add_langs,
                remove_codes,
                max_languages=language_limit,
            ):
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
                    msg = self._build_language_limit_message(
                        decision.instruction_language,
                        max_languages=language_limit,
                    )
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

    def _handle_translation_flow(
        self,
        event: models.MessageEvent,
        sender_name: str,
        translation_enabled: bool | None = None,
        deferred_display_name: str | None = None,
    ) -> bool:
        started = time.perf_counter()
        runtime = self._repo.fetch_translation_runtime_state(event.group_id)
        self._log_translation_stage("runtime_fetched", started, event.group_id)

        plan_key = resolve_effective_plan(runtime.subscription_status, runtime.entitlement_plan)
        if runtime.subscription_status in {"active", "trialing"} and plan_key == FREE_PLAN:
            # 後方互換: entitlement_plan 未保存環境は paid を Pro 扱い
            plan_key = PRO_PLAN
        language_limit = language_limit_for(plan_key)
        raw_languages = self._dedup_language_codes(runtime.group_languages)
        removed_languages: List[str] = []
        if len(raw_languages) > language_limit:
            removed_languages = self._repo.shrink_group_languages(event.group_id, language_limit)
            raw_languages = self._dedup_language_codes(self._repo.fetch_group_languages(event.group_id))
        candidate_languages = self._limit_language_codes(raw_languages, max_languages=language_limit)
        logger.info(
            "Translation flow start | group=%s enabled=%s candidates=%s",
            event.group_id,
            runtime.translation_enabled,
            candidate_languages,
        )

        if not candidate_languages:
            logger.info(
                "group has no language preferences yet; attempting enrollment",
                extra={"group_id": event.group_id, "user_id": event.user_id},
            )
            if self._attempt_language_enrollment(event):
                return True

        if not runtime.translation_enabled:
            logger.info(
                "Translation disabled; sending pause notice",
                extra={"group_id": event.group_id, "user_id": event.user_id},
            )
            # 停止理由に応じた案内を返して終了
            self._send_pause_notice(event)
            return True

        limit = self._quota_limit_for_plan(plan_key)
        stop_translation_on_limit = stop_translation_on_quota(plan_key)

        self._log_translation_stage("before_translation_run", started, event.group_id)

        flow = self._translation_flow.run(
            event=event,
            sender_name=sender_name,
            candidate_languages=candidate_languages,
            stop_translation_on_limit=stop_translation_on_limit,
            limit=limit,
            plan_key=plan_key,
            period_start=runtime.period_start,
            period_end=runtime.period_end,
            quota_anchor_day=runtime.quota_anchor_day,
        )
        self._log_translation_stage("after_translation_run", started, event.group_id)

        if not flow.decision.allowed:
            logger.info(
                "Translation blocked by quota | group=%s plan=%s period=%s usage=%s limit=%s stop_translation=%s notify=%s",
                event.group_id,
                flow.decision.plan_key,
                flow.decision.period_key,
                flow.decision.usage,
                flow.decision.limit,
                flow.decision.stop_translation,
                flow.decision.should_notify,
            )
            if flow.decision.stop_translation:
                self._repo.set_translation_enabled(event.group_id, False)
            if flow.decision.should_notify:
                self._maybe_send_limit_notice(
                    event,
                    flow.decision.limit,
                    flow.decision.plan_key,
                    flow.decision.period_key,
                    runtime.period_end,
                )
            return True

        send_notice_after_translation = flow.decision.should_notify
        period_key = flow.decision.period_key

        if flow.reply_text:
            extra_messages: List[dict] = []
            if removed_languages and event.reply_token:
                base = (
                    f"Your current plan allows up to {language_limit} languages. "
                    "Older language settings were removed automatically: "
                    f"{', '.join(removed_languages)}"
                )
                shrink_notice = self._build_multilingual_interface_message(base, event.group_id)
                extra_messages.append({"type": "text", "text": shrink_notice[:5000]})
            if send_notice_after_translation:
                notice_text, notice_url = self._build_limit_reached_notice_text(
                    event.group_id,
                    flow.decision.plan_key,
                    limit,
                )
                self._repo.set_limit_notice_plan(event.group_id, period_key, plan_key)
                if event.reply_token:
                    messages = extra_messages + [
                        {"type": "text", "text": flow.reply_text[:5000]},
                        {"type": "text", "text": notice_text[:5000]},
                    ]
                    if notice_url:
                        messages.append({"type": "text", "text": notice_url})
                    self._line.reply_messages(event.reply_token, messages)
            else:
                if event.reply_token:
                    if extra_messages:
                        messages = extra_messages + [{"type": "text", "text": flow.reply_text[:5000]}]
                        self._line.reply_messages(event.reply_token, messages)
                    else:
                        self._line.reply_text(event.reply_token, flow.reply_text)

            if deferred_display_name and event.group_id and event.user_id:
                try:
                    self._repo.upsert_group_member_display_name(
                        event.group_id,
                        event.user_id,
                        deferred_display_name,
                    )
                except Exception:
                    logger.exception(
                        "Failed to upsert group member display name after reply",
                        extra={"group_id": event.group_id, "user_id": event.user_id},
                    )

            self._log_translation_stage("after_line_reply", started, event.group_id)
        else:
            logger.warning(
                "Translation finished without reply text | group=%s candidates=%s plan=%s period=%s",
                event.group_id,
                candidate_languages,
                plan_key,
                period_key,
            )

        return True

    # --- subscription helpers ---
    def _handle_subscription_menu(self, event: models.MessageEvent, instruction_lang: str) -> bool:
        status, _period_start, period_end = getattr(self._repo, "get_subscription_period", lambda *_: (None, None, None))(
            event.group_id
        )
        (
            _status,
            entitlement_plan,
            _billing_interval,
            _is_grandfathered,
            _stripe_price_id,
            _period_start2,
            _period_end2,
            _quota_anchor_day,
            _scheduled_target_price_id,
            _scheduled_effective_at,
        ) = getattr(self._repo, "get_subscription_plan", lambda *_: (None, FREE_PLAN, "month", False, None, None, None, None, None, None))(
            event.group_id
        )
        effective_plan = resolve_effective_plan(status, entitlement_plan)
        if status in {"active", "trialing"} and effective_plan == FREE_PLAN:
            effective_plan = PRO_PLAN
        paid = effective_plan in {STANDARD_PLAN, PRO_PLAN}

        portal_url = self._subscription_service.create_portal_url(event.group_id)
        upgrade_url = self._subscription_service.create_checkout_url(event.group_id)

        message = build_subscription_menu_message(
            group_id=event.group_id,
            instruction_lang=instruction_lang,
            status=status,
            period_end=period_end,
            portal_url=portal_url,
            upgrade_url=upgrade_url,
            include_upgrade=effective_plan != PRO_PLAN,
            include_cancel=paid,
            translate=lambda text: self._translate_template(text, instruction_lang, force=True),
            truncate=self._truncate,
            normalize_text=self._normalize_template_text,
        )

        if not message:
            fallback = self._translate_template("Subscription status is unavailable right now.", instruction_lang, force=True)
            if event.reply_token and fallback:
                self._line.reply_text(event.reply_token, fallback[:5000])
            return True

        if event.reply_token:
            self._line.reply_messages(event.reply_token, [message])
        return True

    def _handle_subscription_cancel(self, event: models.MessageEvent, instruction_lang: str) -> bool:
        customer_id, subscription_id, status = getattr(self._repo, "get_subscription_detail", lambda *_: (None, None, None))(
            event.group_id
        )
        active = status in {"active", "trialing"}
        if not subscription_id or not customer_id or not active:
            message = self._translate_interface_single(SUBS_NOT_PRO_TEXT, instruction_lang, event.group_id)
            if event.reply_token and message:
                self._line.reply_text(event.reply_token, message[:5000])
            return True

        confirm = build_subscription_cancel_confirm(
            group_id=event.group_id,
            translate=lambda text: self._translate_template(text, instruction_lang, force=True),
            truncate=self._truncate,
            normalize_text=self._normalize_template_text,
            base_confirm_text=SUBS_CANCEL_CONFIRM_TEXT,
        )
        if event.reply_token and confirm:
            self._line.reply_messages(event.reply_token, [confirm])
        return True

    def _handle_subscription_upgrade(self, event: models.MessageEvent, instruction_lang: str) -> bool:
        # 比較ページのURLを返す（ページ内でプラン選択）
        checkout_url = self._subscription_service.create_checkout_url(event.group_id)
        if not checkout_url:
            message = self._translate_interface_single(SUBS_UPGRADE_LINK_FAIL, instruction_lang, event.group_id)
            if event.reply_token and message:
                self._line.reply_text(event.reply_token, message[:5000])
            return True
        if event.reply_token:
            self._line.reply_text(event.reply_token, checkout_url)
        return True

    def _maybe_send_limit_notice(
        self,
        event: models.MessageEvent,
        limit: int,
        plan_key: str,
        period_key: str,
        period_end: Optional[datetime] = None,
    ) -> None:
        """プラン別に月1回だけ上限通知を送る。"""
        previous_plan = getattr(self._repo, "get_limit_notice_plan", lambda *_: None)(event.group_id, period_key)
        if previous_plan == plan_key:
            # 同一プランで既に通知済み
            return

        self._send_limit_reached_notice(
            event,
            plan_key,
            limit,
            period_key=period_key,
            period_end=period_end,
        )

        setter = getattr(self._repo, "set_limit_notice_plan", None)
        if setter:
            setter(event.group_id, period_key, plan_key)

    def _send_limit_reached_notice(
        self,
        event: models.MessageEvent,
        plan_key: str,
        limit: int,
        *,
        period_key: Optional[str] = None,
        period_end: Optional[datetime] = None,
    ) -> None:
        """上限到達/超過時の統一通知。"""
        notice_text, url = self._build_limit_reached_notice_text(
            event.group_id,
            plan_key,
            limit,
            period_key=period_key,
            period_end=period_end,
        )
        if not event.reply_token:
            return

        messages = [{"type": "text", "text": notice_text[:5000]}]
        if url:
            # URL は左右書字方向混在時に誤判定されやすいため別メッセージで送る
            messages.append({"type": "text", "text": url})
        self._line.reply_messages(event.reply_token, messages)

    def _build_limit_reached_notice_text(
        self,
        group_id: str,
        plan_key: Optional[str] = None,
        limit: int = 0,
        *,
        period_key: Optional[str] = None,
        period_end: Optional[datetime] = None,
        paid: Optional[bool] = None,
    ) -> tuple[str, Optional[str]]:
        normalized = normalize_plan_key(plan_key)
        if paid is True:
            normalized = PRO_PLAN
        elif paid is False:
            normalized = FREE_PLAN

        if normalized == FREE_PLAN:
            base = (
                f"Free quota ({limit:,} messages per month) is exhausted and translation will stop.\n"
                "To continue using the service, please review plans from the link below."
            )
            url = self._subscription_service.create_checkout_url(group_id)
        elif normalized == STANDARD_PLAN:
            reset_date = self._resolve_quota_reset_date(period_key=period_key, period_end=period_end)
            reset_line = (
                f"Translation will resume automatically on {reset_date} (UTC) when the monthly quota resets."
                if reset_date
                else "Translation will resume automatically when the monthly quota resets."
            )
            base = (
                f"The Standard plan monthly limit ({limit:,} messages) has been reached and translation is paused.\n"
                f"{reset_line}\n"
                "To unlock a higher limit now, upgrade to the Pro plan from the link below."
            )
            url = self._subscription_service.create_checkout_url(group_id)
        else:
            reset_date = self._resolve_quota_reset_date(period_key=period_key, period_end=period_end)
            reset_line = (
                f"It will resume automatically on {reset_date} (UTC) when the monthly quota resets."
                if reset_date
                else "It will resume automatically when the monthly quota resets."
            )
            base = (
                f"The Pro plan monthly limit ({limit:,} messages) has been reached and translation has stopped.\n"
                f"{reset_line}"
            )
            url = None

        return self._build_multilingual_notice(
            base,
            group_id,
            url,
            add_missing_link_notice=(paid is not True and normalized != PRO_PLAN),
        )

    def _resolve_quota_reset_date(
        self,
        *,
        period_key: Optional[str],
        period_end: Optional[datetime],
    ) -> Optional[str]:
        if period_end:
            return period_end.astimezone(timezone.utc).date().isoformat()

        if not period_key:
            return None

        try:
            start = datetime.strptime(period_key, "%Y-%m-%d")
            year = start.year + (1 if start.month == 12 else 0)
            month = 1 if start.month == 12 else start.month + 1
            day = min(start.day, monthrange(year, month)[1])
            return datetime(year, month, day).date().isoformat()
        except (TypeError, ValueError):
            return None

    def _build_subscription_menu_message(
        self,
        *,
        group_id: str,
        instruction_lang: str,
        status: Optional[str],
        period_end: Optional[datetime],
        portal_url: Optional[str],
        upgrade_url: Optional[str],
        include_upgrade: bool,
    ) -> Optional[Dict]:
        summary = self._build_subscription_summary_text(status, period_end)
        translated_summary = self._translate_template(summary, instruction_lang, force=True) or summary
        body_text = self._truncate(self._normalize_template_text(translated_summary), 120)

        title = self._translate_template(SUBS_MENU_TITLE, instruction_lang, force=True) or SUBS_MENU_TITLE
        alt_text = self._translate_template(SUBS_MENU_TEXT, instruction_lang, force=True) or SUBS_MENU_TEXT

        actions: List[Dict] = []
        if portal_url:
            label = self._translate_template(SUBS_VIEW_LABEL, instruction_lang, force=True) or SUBS_VIEW_LABEL
            actions.append({"type": "uri", "label": self._truncate(label, 20), "uri": portal_url})

        if status:
            label = self._translate_template(SUBS_CANCEL_LABEL, instruction_lang, force=True) or SUBS_CANCEL_LABEL
            payload = self._encode_subscription_payload({"kind": "cancel", "group_id": group_id})
            actions.append({"type": "postback", "label": self._truncate(label, 20), "data": payload})

        if include_upgrade and upgrade_url:
            label = self._translate_template(SUBS_UPGRADE_LABEL, instruction_lang, force=True) or SUBS_UPGRADE_LABEL
            actions.append({"type": "uri", "label": self._truncate(label, 20), "uri": upgrade_url})

        if not actions:
            return None

        return {
            "type": "template",
            "altText": self._truncate(alt_text, 400),
            "template": {
                "type": "buttons",
                "title": self._truncate(title, 40),
                "text": body_text,
                "actions": actions,
            },
        }


    def _send_pause_notice(self, event: models.MessageEvent) -> None:
        """translation_enabled=False のときに理由別の案内を返す。"""
        runtime_fetcher = getattr(self._repo, "fetch_translation_runtime_state", None)
        if runtime_fetcher:
            runtime = runtime_fetcher(event.group_id)
            plan_key = resolve_effective_plan(runtime.subscription_status, runtime.entitlement_plan)
            if runtime.subscription_status in {"active", "trialing"} and plan_key == FREE_PLAN:
                plan_key = PRO_PLAN
            period_start = runtime.period_start
            period_end = runtime.period_end
            quota_anchor_day = runtime.quota_anchor_day
        else:
            status, period_start, period_end = getattr(
                self._repo,
                "get_subscription_period",
                lambda *_: (None, None, None),
            )(event.group_id)
            plan_key = PRO_PLAN if status in {"active", "trialing"} else FREE_PLAN
            quota_anchor_day = None

        limit = self._quota_limit_for_plan(plan_key)
        period_key = self._current_period_key(
            plan_key=plan_key,
            period_start=period_start,
            period_end=period_end,
            quota_anchor_day=quota_anchor_day,
        )
        usage = self._repo.get_usage(event.group_id, period_key)

        # 上限超過が原因で停止している場合
        if usage >= limit:
            # 翻訳停止中パスでも月1回通知フラグ(limit_notice_plan)を更新する
            self._maybe_send_limit_notice(event, limit, plan_key, period_key, period_end)
            return

        if plan_key in {STANDARD_PLAN, PRO_PLAN}:
            base = "Translation is currently paused. Please try again later or contact the administrator."
            url = None
        else:
            # メンションによる停止中
            logger.info("翻訳停止中", extra={"group_id": event.group_id})
            return

        notice_text, url = self._build_multilingual_notice(
            base,
            event.group_id,
            url,
            add_missing_link_notice=False,
        )
        if not event.reply_token:
            return

        messages = [{"type": "text", "text": notice_text[:5000]}]
        if url:
            messages.append({"type": "text", "text": url})
        self._line.reply_messages(event.reply_token, messages)

    def _build_multilingual_notice(
        self,
        base_text: str,
        group_id: str,
        url: Optional[str],
        *,
        add_missing_link_notice: bool = True,
    ) -> tuple[str, Optional[str]]:
        """案内文と言語混在時に崩れないための URL を分離して返す。"""
        translated_block = self._build_multilingual_interface_message(base_text, group_id)
        lines = [translated_block]
        if not url and add_missing_link_notice:
            lines.append("(Unable to generate purchase link at this time, please contact administrator.)")
        notice_text = "\n\n".join(filter(None, lines))
        return notice_text, url

    # 後方互換：テストや既存コードが呼ぶ旧インターフェースをサービスに委譲
    def _build_checkout_url(self, group_id: str) -> Optional[str]:
        return self._subscription_service.create_checkout_url(group_id)

    # 現在の課金周期を識別するキーを取得
    def _current_period_key(
        self,
        *,
        plan_key: str,
        period_start: Optional[datetime],
        period_end: Optional[datetime],
        quota_anchor_day: Optional[int],
    ) -> str:
        return self._quota.compute_period_key(
            plan_key=plan_key,
            period_start=period_start,
            period_end=period_end,
            quota_anchor_day=quota_anchor_day,
        )

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

        # 英語はベース文をそのまま使用し、翻訳リクエストには含めない
        translation_targets = [
            lang for lang in targets_list if not lang.lower().startswith("en")
        ]

        translations = self._invoke_translation_with_retry(
            sender_name="System",
            message_text=USAGE_MESSAGE,
            timestamp=datetime.now(timezone.utc),
            context=[],
            candidate_languages=translation_targets,
        )

        targets_lower = {lang.lower() for lang in targets_list}

        lines: List[str] = []
        if any(lang.startswith("en") for lang in targets_lower):
            lines.append(_wrap_bidi_isolate(USAGE_MESSAGE, "en"))

        seen_langs = set()
        for item in translations:
            lang_code = item.lang.lower()
            if lang_code in seen_langs:
                continue
            seen_langs.add(lang_code)
            cleaned = strip_source_echo(USAGE_MESSAGE, item.text)
            lines.append(_wrap_bidi_isolate(cleaned, lang_code))

        return "\n\n".join(lines)[:MAX_REPLY_LENGTH]

    def _build_unknown_response(self, instruction_lang: str) -> str:
        translations = self._invoke_translation_with_retry(
            sender_name="System",
            message_text=UNKNOWN_INSTRUCTION_BASE,
            timestamp=datetime.now(timezone.utc),
            context=[],
            candidate_languages=[instruction_lang] if instruction_lang else [],
            allow_same_language=True,
        )
        if not translations:
            return self._normalize_bullet_newlines(UNKNOWN_INSTRUCTION_BASE)
        text = strip_source_echo(UNKNOWN_INSTRUCTION_BASE, translations[0].text)
        normalized = self._normalize_bullet_newlines(text or UNKNOWN_INSTRUCTION_BASE)
        return normalized

    def _translate_interface_single(self, base_text: str, instruction_lang: str, group_id: str) -> str:
        """インターフェース文言を 1 言語で返す。instruction_lang が無い場合はグループの主要言語を使用。"""

        # instruction_lang 優先
        if instruction_lang and self._interface_translation:
            try:
                translations = self._interface_translation.translate(base_text, [instruction_lang])
                if translations:
                    cleaned = strip_source_echo(base_text, translations[0].text)
                    if cleaned or translations[0].text:
                        return cleaned or translations[0].text or base_text
            except Exception:  # pylint: disable=broad-except
                logger.warning("translate_interface_single failed", exc_info=True)

        # グループ主要言語へフォールバック
        languages = getattr(self._repo, "fetch_group_languages", lambda *_: [])(group_id)
        primary = None
        for lang in languages:
            if lang and not lang.lower().startswith("en"):
                primary = lang
                break

        if primary and self._interface_translation:
            try:
                translations = self._interface_translation.translate(base_text, [primary])
                if translations:
                    cleaned = strip_source_echo(base_text, translations[0].text)
                    return cleaned or translations[0].text or base_text
            except Exception:  # pylint: disable=broad-except
                logger.warning("translate_interface_single fallback failed", exc_info=True)

        return base_text

    def _normalize_bullet_newlines(self, text: str) -> str:
        """箇条書きのハイフンの前に改行を強制して読みやすくする。"""
        return re.sub(r"(?<!\n)(- )", "\n- ", text)

    def _build_language_limit_message(self, instruction_lang: str, *, max_languages: Optional[int] = None) -> str:
        limit = max_languages if max_languages is not None else self._max_group_languages
        base = LANGUAGE_LIMIT_MESSAGE_EN.format(limit=limit)
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
        *,
        allow_same_language: bool = False,
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
                allow_same_language=allow_same_language,
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

    @staticmethod
    def _looks_english(text: str) -> bool:
        """簡易的に英語らしいか判定（非英語文字を含む場合は除外）。"""
        if not text:
            return False
        # CJK/ハングル/アラビア文字が含まれていれば英語扱いしない
        if re.search(r"[\u3040-\u30ff\u4e00-\u9fff\u3130-\u318f\uac00-\ud7af\u0600-\u06ff]", text):
            return False
        # アルファベットが含まれていれば英語らしいとみなす
        return bool(re.search(r"[A-Za-z]", text))

    def _choose_interface_base_text(self, decision: models.CommandDecision, default_en: str) -> str:
        """インターフェース文言のベース英語文を選択する。

        ack_text は指示言語で生成されるため、そのまま使うと英語スロットに他言語が入って
        重複表示になる。英語らしい場合のみ採用し、それ以外は英語デフォルトを返す。
        """
        ack = (decision.ack_text or "").strip()
        if ack and self._looks_english(ack):
            return ack
        return default_en

    def _build_multilingual_interface_message(self, base_text: str, group_id: str) -> str:
        languages = self._limit_language_codes(self._repo.fetch_group_languages(group_id))

        # ベース文は英語前提でそのまま使用する
        base_text_en = base_text or ""

        translate_targets = [lang for lang in languages if lang.lower() != "en"]
        translations = self._invoke_interface_translation_with_retry(base_text_en, translate_targets)

        if not translations:
            return base_text_en

        text_by_lang = {}
        for item in translations:
            lowered = item.lang.lower()
            if lowered in text_by_lang:
                continue
            cleaned = strip_source_echo(base_text_en, item.text)
            text_by_lang[lowered] = cleaned or item.text or base_text_en

        lines: List[str] = []
        for lang in languages:
            lowered = lang.lower()
            if lowered == "en":
                text = base_text_en  # ベース英語文をそのまま使う
            else:
                text = text_by_lang.get(lowered, base_text_en)
            text = (text or base_text_en).strip()
            lines.append(_wrap_bidi_isolate(text, lowered))

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


    def _resolve_sender_name(self, event: models.MessageEvent) -> tuple[str, str | None]:
        if not event.user_id or not event.group_id:
            return event.user_id or "Unknown", None

        cached = self._repo.get_group_member_display_name(event.group_id, event.user_id)
        if cached:
            return cached, None

        name = self._line.get_display_name(event.sender_type, event.group_id, event.user_id)
        if name:
            return name, name
        return event.user_id, None

    def _log_translation_stage(self, stage: str, started: float, group_id: str) -> None:
        logger.info(
            "Translation stage | stage=%s elapsed_ms=%.2f group=%s",
            stage,
            (time.perf_counter() - started) * 1000,
            group_id,
        )

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

    def _resolve_effective_plan_for_group(self, group_id: str) -> str:
        runtime_fetcher = getattr(self._repo, "fetch_translation_runtime_state", None)
        if runtime_fetcher:
            try:
                runtime = runtime_fetcher(group_id)
                plan_key = resolve_effective_plan(runtime.subscription_status, runtime.entitlement_plan)
                if runtime.subscription_status in {"active", "trialing"} and plan_key == FREE_PLAN:
                    return PRO_PLAN
                return plan_key
            except Exception:  # pylint: disable=broad-except
                logger.debug("Failed to resolve plan from runtime state", exc_info=True)

        plan_fetcher = getattr(self._repo, "get_subscription_plan", None)
        if plan_fetcher:
            try:
                status, entitlement_plan, *_rest = plan_fetcher(group_id)
                plan_key = resolve_effective_plan(status, entitlement_plan)
                if status in {"active", "trialing"} and plan_key == FREE_PLAN:
                    return PRO_PLAN
                return plan_key
            except Exception:  # pylint: disable=broad-except
                logger.debug("Failed to resolve plan from subscription plan", exc_info=True)

        status_fetcher = getattr(self._repo, "get_subscription_status", None)
        if status_fetcher:
            try:
                status = status_fetcher(group_id)
                if status in {"active", "trialing"}:
                    return PRO_PLAN
            except Exception:  # pylint: disable=broad-except
                logger.debug("Failed to resolve plan from subscription status", exc_info=True)

        status, _period_start, _period_end = getattr(
            self._repo,
            "get_subscription_period",
            lambda *_: (None, None, None),
        )(group_id)
        if status in {"active", "trialing"}:
            return PRO_PLAN
        return FREE_PLAN

    def _quota_limit_for_plan(self, plan_key: str) -> int:
        normalized = normalize_plan_key(plan_key)
        if normalized == FREE_PLAN:
            return self._free_quota
        if normalized == STANDARD_PLAN:
            return self._standard_quota
        if normalized == PRO_PLAN:
            return self._pro_quota
        return monthly_quota_for(normalized)

    def _would_exceed_language_limit(
        self,
        current_langs: Sequence[str],
        add_langs: Sequence[Tuple[str, str]],
        remove_codes: Sequence[str],
        *,
        max_languages: Optional[int] = None,
    ) -> bool:
        limit = max_languages if max_languages is not None else self._max_group_languages
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
        return final_count > limit

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

    def _limit_language_codes(self, languages: Sequence[str], max_languages: Optional[int] = None) -> List[str]:
        limit = max_languages if max_languages is not None else self._max_group_languages
        deduped = self._dedup_language_codes(languages)
        return deduped[:limit]

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
