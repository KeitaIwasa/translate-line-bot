from __future__ import annotations

import logging

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...domain.services.interface_translation_service import InterfaceTranslationService
from ...domain.services.subscription_service import SubscriptionService
from ...presentation.multilingual_message import build_multilingual_message, dedup_lang_codes

logger = logging.getLogger(__name__)


class MemberLeftHandler:
    def __init__(
        self,
        line_client: LinePort,
        repo: MessageRepositoryPort,
        subscription_service: SubscriptionService,
        interface_translation: InterfaceTranslationService | None = None,
    ) -> None:
        self._line = line_client
        self._repo = repo
        self._subscription_service = subscription_service
        self._interface_translation = interface_translation

    def handle(self, event: models.MemberLeftEvent) -> None:
        group_id = event.group_id
        if not group_id:
            return

        owner_user_id = getattr(self._repo, "get_billing_owner_user_id", lambda *_: None)(group_id)
        for user_id in event.left_user_ids:
            self._repo.mark_group_member_left(group_id, user_id)

        if not owner_user_id or owner_user_id not in set(event.left_user_ids):
            return

        result = self._subscription_service.reserve_cancellation_on_owner_leave(group_id)
        if not result:
            logger.warning("Failed to reserve owner-left cancellation", extra={"group_id": group_id})
            return

        period_end = result.get("current_period_end")
        until = period_end.date().isoformat() if period_end else "the current billing period end"
        languages = dedup_lang_codes(self._repo.fetch_group_languages(group_id))
        group_message_en = (
            "The billing owner has left this LINE group.\n"
            f"The current paid plan remains active until {until} and will then stop automatically.\n"
            "To continue after that date, a current group member must open billing management and register a new card."
        )
        owner_dm_en = (
            "You left the LINE group that owns this subscription.\n"
            f"Auto-renew has been set to stop at period end ({until}).\n"
            "If the group wants to continue after that date, a current member must register a new card from billing management."
        )
        group_message = build_multilingual_message(
            base_text=group_message_en,
            languages=languages,
            translator=self._interface_translation,
            logger=logger,
            warning_log="Owner-left group notice translation failed",
        )
        owner_dm_message = build_multilingual_message(
            base_text=owner_dm_en,
            languages=languages,
            translator=self._interface_translation,
            logger=logger,
            warning_log="Owner-left DM notice translation failed",
        )
        checkout_url = self._subscription_service.create_checkout_url(group_id)
        if checkout_url:
            group_message = f"{group_message}\n\n{checkout_url}".strip()
        group_push_result = "skipped"
        dm_push_result = "skipped"
        try:
            self._line.push_text(group_id, group_message)
            group_push_result = "success"
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to push owner-left notice", extra={"group_id": group_id}, exc_info=True)
            group_push_result = "failed"

        try:
            self._line.push_text(owner_user_id, owner_dm_message)
            dm_push_result = "success"
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to push owner-left DM notice",
                extra={"group_id": group_id, "owner_user_id": owner_user_id},
                exc_info=True,
            )
            dm_push_result = "failed"

        logger.info(
            "Owner-left cancellation notice processed",
            extra={
                "group_id": group_id,
                "owner_user_id": owner_user_id,
                "period_end": period_end.isoformat() if period_end else None,
                "group_push_result": group_push_result,
                "dm_push_result": dm_push_result,
            },
        )
