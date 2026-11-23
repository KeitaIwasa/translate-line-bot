from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...presentation.guidance_cards import member_joined_card
from ...presentation.rich_message import deliver_reply

logger = logging.getLogger(__name__)


class MemberJoinedHandler:
    def __init__(self, line_client: LinePort, repo: MessageRepositoryPort) -> None:
        self._line = line_client
        self._repo = repo

    def handle(self, event: models.MemberJoinedEvent) -> None:
        if not (event.group_id and event.reply_token):
            return

        event_time = _event_timestamp(event) or datetime.now(timezone.utc)
        bot_joined_at = self._repo.fetch_bot_joined_at(event.group_id)

        for user_id in event.joined_user_ids:
            if not user_id:
                continue
            self._repo.ensure_group_member(event.group_id, user_id)

        if bot_joined_at and (event_time - bot_joined_at) < timedelta(minutes=10):
            return

        joined_names: List[str] = []
        for user_id in event.joined_user_ids:
            if not user_id:
                continue
            name = self._line.get_display_name("group", event.group_id, user_id)
            if name:
                joined_names.append(name)

        prefix = "、".join(joined_names) if joined_names else "everyone"
        deliver_reply(self._line, event.reply_token, member_joined_card(prefix), logger=logger)


def _event_timestamp(event: models.BaseEvent):
    try:
        if event.timestamp:
            return datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
    except Exception:
        return None
    return None
