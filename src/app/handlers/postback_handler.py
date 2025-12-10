from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Tuple
from datetime import datetime, timezone

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...domain.services.interface_translation_service import InterfaceTranslationService
from ...domain.services.subscription_service import SubscriptionService
from ...domain.services.language_settings_service import LanguageSettingsService
from ...presentation.reply_formatter import strip_source_echo
from ..subscription_texts import SUBS_CANCEL_CONFIRM_TEXT, SUBS_CANCEL_DONE_TEXT, SUBS_CANCEL_FAIL_TEXT
from ..subscription_postback import decode_postback_payload, encode_subscription_payload
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
            bundle = self._lang_settings.confirm(
                group_id=event.group_id,
                languages=tuples,
                primary_language=primary_language,
                completion_text=payload.get("completion_text"),
                limit_text=payload.get("limit_text"),
            )
            if not bundle:
                logger.info(
                    "Duplicate language confirmation ignored",
                    extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
                )
                return
            if event.reply_token and bundle.texts:
                self._line.reply_text(event.reply_token, bundle.texts[0])
            logger.info(
                "Language preferences saved",
                extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
            )
        elif action == "cancel":
            bundle = self._lang_settings.cancel(
                group_id=event.group_id,
                primary_language=primary_language,
                cancel_text=payload.get("cancel_text"),
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

        if kind == "cancel":
            confirm = build_subscription_cancel_confirm(
                group_id=group_id,
                translate=lambda text: self._translate_for_group(text, group_id),
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
            customer_id, subscription_id, _status = getattr(self._repo, "get_subscription_detail", lambda *_: (None, None, None))(group_id)
            if not subscription_id or not customer_id:
                message = self._build_multilingual_message("No active subscription found for this group.", group_id)
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
