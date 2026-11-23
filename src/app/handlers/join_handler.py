from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...domain.services.language_mention import join_settings_page_note
from ...presentation.guidance_cards import JOIN_GUIDANCE, join_card
from ...presentation.rich_message import deliver_reply

logger = logging.getLogger(__name__)

# 参加案内の原文。Flex の本文と、送信失敗時の平文で同じ文字列を使う。
GROUP_PROMPT_MESSAGE = JOIN_GUIDANCE


class JoinHandler:
    def __init__(self, line_client: LinePort, repo: MessageRepositoryPort, language_page_url=None) -> None:
        self._line = line_client
        self._repo = repo
        self._language_page_url = language_page_url

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
        deliver_reply(
            self._line,
            event.reply_token,
            join_card(_join_page_note(self._language_page_url, event.group_id)),
            logger=logger,
        )


def _join_page_note(language_page_url, group_id: str) -> str:
    """参加案内に添える言語ページ。URL は Flex のボタンへ移す。"""

    if not language_page_url:
        return ""
    try:
        url = language_page_url(group_id) or ""
    except Exception:
        logger.debug("Failed to build language settings url", exc_info=True)
        return ""
    return join_settings_page_note(url)


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
