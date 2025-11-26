from __future__ import annotations

import logging
from typing import Dict, Protocol

from ..domain import models

logger = logging.getLogger(__name__)


class Handler(Protocol):
    def handle(self, event: models.BaseEvent) -> None: ...


class Dispatcher:
    def __init__(self, handlers: Dict[str, Handler]) -> None:
        self._handlers = handlers

    def dispatch(self, event: models.BaseEvent) -> None:
        handler = self._handlers.get(event.event_type)
        if not handler:
            logger.debug("No handler for event type %s", event.event_type)
            return
        handler.handle(event)
