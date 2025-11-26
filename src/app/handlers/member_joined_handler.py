from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort


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
        message = (
            f"Hello {prefix} !\n\n"
            "When you want to change the interpreter's language settings, please remove this bot from the group once and then invite it again!\n\n"
            "通訳の言語設定を変更するときは、このボットを一度グループから削除してから、再度招待してね！\n\n"
            "如果你想更改口译语言设置，请先将此机器人从群组中删除，然后再重新邀请它！\n\n"
            "หากคุณต้องการเปลี่ยนการตั้งค่าภาษาของล่าม กรุณานำบอทนี้ออกจากกลุ่มก่อน แล้วค่อยเชิญกลับมาอีกครั้ง!"
        )
        self._line.reply_text(event.reply_token, message)


def _event_timestamp(event: models.BaseEvent):
    try:
        if event.timestamp:
            return datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
    except Exception:
        return None
    return None
