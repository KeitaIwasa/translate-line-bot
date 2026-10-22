from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Tuple

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...domain.services.interface_translation_service import InterfaceTranslationService
from ...domain.services.subscription_service import SubscriptionService
from ...domain.services.current_languages_question import (
    current_languages_base_text,
    current_languages_localized_text,
)
from ...domain.services.language_mention import (
    howto_prompt,
    language_quick_replies,
    menu_family,
    settings_page_line,
)
from ...domain.services.language_settings_service import LanguageSettingsService
from ...presentation.reply_formatter import strip_source_echo
from ..subscription_texts import (
    SUBS_CANCEL_CONFIRM_TEXT,
    SUBS_CANCEL_DONE_TEXT,
    SUBS_CANCEL_FAIL_TEXT,
    SUBS_NOT_PRO_TEXT,
    SUBS_OWNER_ONLY_TEXT,
)
from ..subscription_postback import decode_postback_payload
from ..subscription_templates import build_subscription_cancel_confirm

logger = logging.getLogger(__name__)



class PostbackHandler:
    def __init__(
        self,
        line_client: LinePort,
        repo: MessageRepositoryPort,
        max_group_languages: int = 5,
        interface_translation: InterfaceTranslationService | None = None,
        subscription_service: SubscriptionService | None = None,
        language_settings_service: LanguageSettingsService | None = None,
    ) -> None:
        self._line = line_client
        self._repo = repo
        self._max_group_languages = max_group_languages
        self._interface_translation = interface_translation
        self._subscription_service = subscription_service or SubscriptionService(repo, "", "")
        self._lang_settings = language_settings_service or LanguageSettingsService(
            repo,
            preference_analyzer=None,  # type: ignore[arg-type]
            interface_translation=interface_translation or InterfaceTranslationService(None),  # type: ignore[arg-type]
            max_group_languages=max_group_languages,
        )

    def handle(self, event: models.PostbackEvent) -> None:
        if not event.data or not event.reply_token or not event.group_id:
            return

        if event.data.startswith("langmenu="):
            self._handle_language_menu(event)
            return

        payload = decode_postback_payload(event.data)
        if not payload:
            logger.debug("Ignoring unknown postback", extra={"data": event.data})
            return

        kind = payload.get("kind")
        if kind == "language_confirm":
            self._handle_language_confirm(event, payload)
            return

        if kind and kind.startswith("cancel"):
            self._handle_subscription_cancel(event, payload)
            return

        logger.debug("Unhandled postback kind", extra={"kind": kind})

    def _handle_language_confirm(self, event: models.PostbackEvent, payload: Dict) -> None:
        action = payload.get("action")
        primary_language = (payload.get("primary_language") or "").lower()
        if action == "confirm":
            langs = payload.get("languages") or []
            tuples: List[Tuple[str, str]] = [
                (item.get("code", ""), item.get("name", "")) for item in langs if item.get("code")
            ]
            issued_at = payload.get("issued_at")
            confirm_kwargs = {}
            if isinstance(issued_at, str) and issued_at:
                confirm_kwargs["issued_at"] = issued_at
            bundle = self._lang_settings.confirm(
                group_id=event.group_id,
                languages=tuples,
                primary_language=primary_language,
                completion_text=payload.get("completion_text"),
                limit_text=payload.get("limit_text"),
                settings_url=self._language_settings_url(event.group_id, ""),
                **confirm_kwargs,
            )
            if not bundle:
                logger.info(
                    "Duplicate language confirmation ignored",
                    extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
                )
                return
            if event.reply_token and bundle.texts:
                self._line.reply_text(event.reply_token, bundle.texts[0])
            if bundle.notice_kind == "stale":
                logger.info(
                    "Stale language confirmation ignored",
                    extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
                )
                return
            logger.info(
                "Language preferences saved",
                extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
            )
        elif action == "cancel":
            # 確認のキャンセルは、このポストバックをユーザーが押したときだけ。時間切れでは送らない
            issued_at = payload.get("issued_at")
            cancel_kwargs = {}
            if isinstance(issued_at, str) and issued_at:
                cancel_kwargs["issued_at"] = issued_at
            bundle = self._lang_settings.cancel(
                group_id=event.group_id,
                primary_language=primary_language,
                cancel_text=payload.get("cancel_text"),
                **cancel_kwargs,
            )
            if not bundle:
                logger.info(
                    "Duplicate language cancellation ignored",
                    extra={"group_id": event.group_id, "user_id": event.user_id},
                )
                return
            if event.reply_token and bundle.texts:
                self._line.reply_text(event.reply_token, bundle.texts[0])
            logger.info("Language enrollment cancelled", extra={"group_id": event.group_id, "user_id": event.user_id})

    def _handle_language_menu(self, event: models.PostbackEvent) -> None:
        """Quick Reply の「今の言語」「使い方」。グループの誰が押しても同じグループに返す。"""

        if not event.reply_token or not event.group_id:
            return
        action = event.data.split("=", 1)[1]
        records = self._language_records(event.group_id)
        family = menu_family([code for code, _name in records])
        url = self._language_settings_url(event.group_id, family)
        if action == "help":
            text = howto_prompt(family=family)
        else:
            localized = current_languages_localized_text(records, family)
            text = localized if localized is not None else current_languages_base_text(records)
        line = settings_page_line(family, url)
        if line:
            text = f"{text}\n\n{line}"
        quick_reply = language_quick_replies(family, url)
        message = {"type": "text", "text": text[:5000], "quickReply": {"items": quick_reply}}
        reply_messages = getattr(self._line, "reply_messages", None)
        if reply_messages:
            reply_messages(event.reply_token, [message])
            return
        self._line.reply_text(event.reply_token, message["text"])

    def _language_settings_url(self, group_id: str, family: str) -> str:
        builder = getattr(self._subscription_service, "create_language_settings_url", None)
        if not builder or not group_id:
            return ""
        try:
            return builder(group_id, family=family) or ""
        except Exception:
            logger.debug("Failed to build language settings url", exc_info=True)
            return ""

    def _language_records(self, group_id: str) -> list:
        fetcher = getattr(self._repo, "fetch_group_language_records", None)
        if fetcher:
            try:
                rows = fetcher(group_id) or []
            except Exception:
                rows = []
        else:
            try:
                rows = self._repo.fetch_group_languages(group_id) or []
            except Exception:
                rows = []
        records = []
        for row in rows:
            if isinstance(row, str):
                records.append((row, ""))
            else:
                code = row[0] if row else ""
                name = row[1] if row and len(row) > 1 and isinstance(row[1], str) else ""
                records.append((code or "", name or ""))
        return records

    def _build_multilingual_message(self, base_text: str, group_id: str) -> str:
        languages = self._repo.fetch_group_languages(group_id)
        targets = [lang for lang in languages if lang and not lang.lower().startswith("en")]
        if not self._interface_translation or not targets:
            return base_text

        translations = []
        try:
            translations = self._interface_translation.translate(base_text, targets)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Interface translation failed", exc_info=True)
            return base_text

        lines: List[str] = [base_text]
        seen = set()
        for item in translations:
            lowered = (item.lang or "").lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned = strip_source_echo(base_text, item.text)
            text = (cleaned or item.text or base_text).strip()
            if text and text not in lines:
                lines.append(text)

        return "\n\n".join(lines)[:5000]

    def _translate_for_group(self, base_text: str, group_id: str) -> str:
        languages = self._repo.fetch_group_languages(group_id)
        primary = None
        for lang in languages:
            if lang and not lang.lower().startswith("en"):
                primary = lang
                break
        if not primary or not self._interface_translation:
            return base_text

        try:
            translations = self._interface_translation.translate(base_text, [primary])
            if translations:
                cleaned = strip_source_echo(base_text, translations[0].text)
                return cleaned or translations[0].text or base_text
        except Exception:  # pylint: disable=broad-except
            logger.warning("translate_for_group failed", exc_info=True)
        return base_text

    def _translate_for_instruction_lang(self, base_text: str, instruction_lang: str, group_id: str) -> str:
        """ボタンテンプレートの言語に合わせて単一言語で翻訳する。"""

        if instruction_lang and self._interface_translation:
            try:
                translations = self._interface_translation.translate(base_text, [instruction_lang])
                if translations:
                    cleaned = strip_source_echo(base_text, translations[0].text)
                    return cleaned or translations[0].text or base_text
            except Exception:  # pylint: disable=broad-except
                logger.warning("translate_for_instruction_lang failed", exc_info=True)

        return self._translate_for_group(base_text, group_id)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _handle_subscription_cancel(self, event: models.PostbackEvent, payload: Dict) -> None:
        kind = payload.get("kind")
        group_id = event.group_id
        if not group_id:
            return

        instruction_lang = payload.get("instruction_lang", "")
        if not self._can_manage_subscription(group_id, event.user_id):
            message = self._translate_for_instruction_lang(SUBS_OWNER_ONLY_TEXT, instruction_lang, group_id)
            if event.reply_token:
                self._line.reply_text(event.reply_token, message)
            return
        customer_id, subscription_id, status = getattr(self._repo, "get_subscription_detail", lambda *_: (None, None, None))(group_id)
        active = status in {"active", "trialing"}

        if kind == "cancel":
            if not subscription_id or not customer_id or not active:
                message = self._translate_for_instruction_lang(SUBS_NOT_PRO_TEXT, instruction_lang, group_id)
                if event.reply_token:
                    self._line.reply_text(event.reply_token, message)
                return

            confirm = build_subscription_cancel_confirm(
                group_id=group_id,
                translate=lambda text: self._translate_for_instruction_lang(text, instruction_lang, group_id),
                truncate=self._truncate,
                normalize_text=lambda x: x,
                base_confirm_text=SUBS_CANCEL_CONFIRM_TEXT,
            )
            if event.reply_token and confirm:
                self._line.reply_messages(event.reply_token, [confirm])
            return

        if kind == "cancel_reject":
            message = self._build_multilingual_message("Subscription cancellation aborted.", group_id)
            if event.reply_token:
                self._line.reply_text(event.reply_token, message)
            return

        if kind == "cancel_confirm":
            if not subscription_id or not customer_id or not active:
                message = self._translate_for_instruction_lang(SUBS_NOT_PRO_TEXT, instruction_lang, group_id)
                if event.reply_token:
                    self._line.reply_text(event.reply_token, message)
                return

            result = self._subscription_service.cancel_subscription(group_id)
            if result:
                if event.reply_token:
                    self._line.reply_text(event.reply_token, self._build_multilingual_message(SUBS_CANCEL_DONE_TEXT, group_id))
            else:
                if event.reply_token:
                    self._line.reply_text(event.reply_token, self._build_multilingual_message(SUBS_CANCEL_FAIL_TEXT, group_id))

    def _can_manage_subscription(self, group_id: str, user_id: str | None) -> bool:
        if not user_id:
            return False
        if getattr(self._repo, "is_billing_owner_lost", lambda *_: False)(group_id):
            return False
        owner_id = getattr(self._repo, "get_billing_owner_user_id", lambda *_: None)(group_id)
        if owner_id:
            return owner_id == user_id
        is_member = getattr(self._repo, "is_group_member", lambda *_: True)(group_id, user_id)
        return bool(is_member)

def _build_completion_message(languages) -> str:
    names = [name or code for code, name in languages if code]
    filtered = [name for name in names if name]
    if not filtered:
        return "Translation languages have been updated."
    if len(filtered) == 1:
        joined = filtered[0]
        return f"{joined} has been set as the translation language."
    if len(filtered) == 2:
        joined = " and ".join(filtered)
    else:
        joined = ", ".join(filtered[:-1]) + ", and " + filtered[-1]
    return f"{joined} have been set as the translation languages."


def _build_cancel_message() -> str:
    return "Language update has been cancelled. Please tell me all languages again."


def _dedup_languages(languages: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    results: List[Tuple[str, str]] = []
    for code, name in languages:
        lowered = (code or "").lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        results.append((lowered, name))
    return results
