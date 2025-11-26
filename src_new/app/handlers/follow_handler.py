from __future__ import annotations

from ...domain import models
from ...domain.ports import LinePort

DIRECT_GREETING = (
    "Thanks for adding me! Please invite me into a group so I can help with multilingual translation."
)


class FollowHandler:
    def __init__(self, line_client: LinePort) -> None:
        self._line = line_client

    def handle(self, event: models.FollowEvent) -> None:
        if not event.reply_token:
            return
        self._line.reply_text(event.reply_token, DIRECT_GREETING)
