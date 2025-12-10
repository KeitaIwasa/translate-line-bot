from __future__ import annotations

from datetime import datetime, timezone

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort

GROUP_PROMPT_MESSAGE = (
    "I'm a multilingual translation bot. Please tell me the languages you want to translate to.\n\n"
    "多言語翻訳ボットです。翻訳したい言語を教えてください。\n\n"
    "我是一个多语言翻译机器人。请告诉我你想要翻译成哪些语言。\n\n"
    "ฉันเป็นบอทแปลหลายภาษา กรุณาบอกฉันว่าคุณต้องการแปลเป็นภาษาใดบ้าง\n\n"
    "ex) English, 中文, 日本語, ไทย"
)


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
        self._line.reply_text(event.reply_token, GROUP_PROMPT_MESSAGE)


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
