from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort
from ...domain.services.language_mention import join_settings_page_note

logger = logging.getLogger(__name__)

GROUP_PROMPT_MESSAGE = (
    "I'm a multilingual translation bot. Please tell me the languages you want to translate to.\n\n"
    "多言語翻訳ボットです。翻訳したい言語を教えてください。\n\n"
    "我是一个多语言翻译机器人。请告诉我你想要翻译成哪些语言。\n\n"
    "ฉันเป็นบอทแปลหลายภาษา กรุณาบอกฉันว่าคุณต้องการแปลเป็นภาษาใดบ้าง\n\n"
    "ex) English, 中文, 日本語, ไทย"
)


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
        self._line.reply_text(event.reply_token, _join_message(self._language_page_url, event.group_id))


def _join_message(language_page_url, group_id: str) -> str:
    if not language_page_url:
        return GROUP_PROMPT_MESSAGE
    try:
        url = language_page_url(group_id) or ""
    except Exception:
        logger.debug("Failed to build language settings url", exc_info=True)
        return GROUP_PROMPT_MESSAGE
    note = join_settings_page_note(url)
    if not note:
        return GROUP_PROMPT_MESSAGE
    return f"{GROUP_PROMPT_MESSAGE}\n\n{note}"


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
