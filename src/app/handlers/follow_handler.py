from __future__ import annotations

from ...domain import models
from ...domain.ports import LinePort


class FollowHandler:
    def __init__(self, line_client: LinePort) -> None:
        self._line = line_client

    def handle(self, event: models.FollowEvent) -> None:
        # 挨拶メッセージはLINE公式アカウントのコンソールで設定するため、ここでは何もしない
        return
