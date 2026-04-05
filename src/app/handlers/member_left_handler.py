from __future__ import annotations

import logging

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...domain.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


class MemberLeftHandler:
    def __init__(self, line_client: LinePort, repo: MessageRepositoryPort, subscription_service: SubscriptionService) -> None:
        self._line = line_client
        self._repo = repo
        self._subscription_service = subscription_service

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
        message = (
            "The billing owner has left this LINE group.\n"
            f"The current paid plan remains active until {until} and will then stop.\n"
            "If you want to continue after that, a current group member must open billing management and register a new card."
        )
        try:
            self._line.push_text(group_id, message)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to push owner-left notice", extra={"group_id": group_id}, exc_info=True)
