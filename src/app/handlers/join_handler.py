from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...presentation.guidance_cards import JOIN_GUIDANCE, join_card
from ...presentation.rich_message import deliver_reply

logger = logging.getLogger(__name__)

# 参加案内の原文。Flex の本文と、送信失敗時の平文で同じ文字列を使う。
GROUP_PROMPT_MESSAGE = JOIN_GUIDANCE


class JoinHandler:
    def __init__(self, line_client: LinePort, repo: MessageRepositoryPort) -> None:
        self._line = line_client
        self._repo = repo

    def handle(self, event: models.JoinEvent) -> None:
        if not (event.group_id and event.reply_token):
            return
        join_time = _event_timestamp(event) or datetime.now(timezone.utc)
        group_name = _fetch_group_name_safe(self._line, event.group_id)
        self._repo.record_bot_joined_at(event.group_id, join_time)
        if group_name:
            self._repo.upsert_group_name(event.group_id, group_name)
        self._repo.reset_group_language_settings(event.group_id)
        self._repo.set_translation_enabled(event.group_id, False)
        deliver_reply(self._line, event.reply_token, join_card(), logger=logger)


def _event_timestamp(event: models.BaseEvent):
    try:
        if event.timestamp:
            return datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
    except Exception:
        return None
    return None


def _fetch_group_name_safe(line_client: LinePort, group_id: str) -> str | None:
    """グループ名取得でエラーが出ても処理を継続するためのラッパー。"""
    try:
        return line_client.get_group_name(group_id)
    except Exception:  # pylint: disable=broad-except
        return None
