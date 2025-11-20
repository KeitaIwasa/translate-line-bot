from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Sequence

from langdetect import LangDetectException, detect

from .gemini_client import ContextMessage as GeminiContextMessage
from .gemini_client import GeminiClient, SourceMessage, Translation

logger = logging.getLogger(__name__)


def detect_language(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return ""


class TranslationService:
    def __init__(self, gemini_client: GeminiClient) -> None:
        self._gemini = gemini_client

    def translate(
        self,
        sender_name: str,
        message_text: str,
        timestamp: datetime,
        context_messages: Iterable[GeminiContextMessage],
        candidate_languages: Sequence[str],
    ) -> List[Translation]:
        detected_lang = detect_language(message_text)
        filtered_targets = [lang for lang in candidate_languages if lang and lang.lower() != detected_lang.lower()]

        if not filtered_targets:
            logger.info("No target languages after filtering", extra={"detected": detected_lang})
            return []

        source = SourceMessage(sender_name=sender_name, text=message_text, timestamp=timestamp)
        return self._gemini.translate(source, context_messages, list(dict.fromkeys(filtered_targets)))
