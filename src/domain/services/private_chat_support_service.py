from __future__ import annotations

import logging
from dataclasses import dataclass

from ..models import PrivateChatResponse
from ..ports import MessageRepositoryPort, PrivateChatResponderPort

logger = logging.getLogger(__name__)

DEFAULT_PRIVATE_CHAT_ERROR_MESSAGE = (
    "申し訳ありません。現在この質問には回答できません。"
    "時間をおいて、別の言い方で試してください。"
)


@dataclass(frozen=True)
class PrivateChatSupportConfig:
    history_limit: int = 5


class PrivateChatSupportService:
    def __init__(
        self,
        repo: MessageRepositoryPort,
        responder: PrivateChatResponderPort,
        config: PrivateChatSupportConfig | None = None,
    ) -> None:
        self._repo = repo
        self._responder = responder
        self._config = config or PrivateChatSupportConfig()

    def respond(self, user_id: str, input_text: str) -> PrivateChatResponse:
        if not user_id:
            return PrivateChatResponse(
                output_text=DEFAULT_PRIVATE_CHAT_ERROR_MESSAGE,
                safe_input_text=input_text,
                safe_output_text=DEFAULT_PRIVATE_CHAT_ERROR_MESSAGE,
                guardrails_failed=True,
            )

        history = self._repo.fetch_private_conversation(user_id, self._config.history_limit)

        try:
            response = self._responder.respond(input_text=input_text, history=history)
            if not response.output_text:
                return PrivateChatResponse(
                    output_text=DEFAULT_PRIVATE_CHAT_ERROR_MESSAGE,
                    safe_input_text=response.safe_input_text or input_text,
                    safe_output_text=DEFAULT_PRIVATE_CHAT_ERROR_MESSAGE,
                    guardrails_failed=True,
                )
            return response
        except Exception:
            logger.exception("Failed to generate private chat response")
            return PrivateChatResponse(
                output_text=DEFAULT_PRIVATE_CHAT_ERROR_MESSAGE,
                safe_input_text=input_text,
                safe_output_text=DEFAULT_PRIVATE_CHAT_ERROR_MESSAGE,
                guardrails_failed=True,
            )
