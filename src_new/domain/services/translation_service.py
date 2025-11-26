from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Sequence

from langdetect import LangDetectException, detect

from ..models import ContextMessage, TranslationRequest, TranslationResult
from ..ports import TranslationPort

logger = logging.getLogger(__name__)


def detect_language(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return ""


class TranslationService:
    """ドメイン層の翻訳ユースケース（言語判定とターゲット除外をここで担当）。"""

    def __init__(self, translator: TranslationPort) -> None:
        self._translator = translator

    def translate(
        self,
        sender_name: str,
        message_text: str,
        timestamp: datetime,
        context_messages: Iterable[ContextMessage],
        candidate_languages: Sequence[str],
    ) -> List[TranslationResult]:
        detected_lang = detect_language(message_text)
        filtered_targets = [
            lang for lang in candidate_languages if lang and lang.lower() != detected_lang.lower()
        ]

        if not filtered_targets:
            logger.info("No target languages after filtering", extra={"detected": detected_lang})
            return []

        request = TranslationRequest(
            sender_name=sender_name,
            message_text=message_text,
            timestamp=timestamp,
            candidate_languages=list(dict.fromkeys(filtered_targets)),
            context_messages=list(context_messages),
        )
        return self._translator.translate(request)
