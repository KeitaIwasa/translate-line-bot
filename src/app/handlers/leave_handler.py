from __future__ import annotations

import logging

from ...domain import models
from ...domain.ports import MessageRepositoryPort
from ...domain.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


class LeaveHandler:
    """退会イベントでサブスクを自動キャンセルするハンドラ。"""

    def __init__(self, subscription_service: SubscriptionService, repo: MessageRepositoryPort) -> None:
        self._subscription_service = subscription_service
        self._repo = repo

    def handle(self, event: models.LeaveEvent) -> None:
        if not event.group_id:
            return

        # サブスク解約は最大3回リトライする
        for attempt in range(3):
            result = self._subscription_service.cancel_subscription(event.group_id)
            if result:
                logger.info(
                    "Subscription auto-cancelled due to leave",
                    extra={"group_id": event.group_id, "attempt": attempt + 1},
                )
                return
            logger.warning(
                "Auto cancel on leave failed",
                extra={"group_id": event.group_id, "attempt": attempt + 1},
            )
        logger.error("Auto cancel on leave gave up after retries", extra={"group_id": event.group_id})
