from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Sequence

try:
    from langdetect import LangDetectException, detect
except Exception:  # pragma: no cover - optional dependency fallback
    class LangDetectException(Exception):
        pass

    def detect(_text: str) -> str:
        return ""

from ..models import ContextMessage, TranslationRequest, TranslationResult
from ..ports import TranslationPort
from .language_detection_service import LanguageDetectionService

logger = logging.getLogger(__name__)


def detect_language(text: str) -> str:
    try:
        return detect(text)
    except LangDetectException:
        return ""


class TranslationService:
    """ドメイン層の翻訳ユースケース（言語判定とターゲット除外をここで担当）。"""

    def __init__(self, translator: TranslationPort, language_detector: LanguageDetectionService | None = None) -> None:
        self._translator = translator
        self._language_detector = language_detector or LanguageDetectionService()

    def translate(
        self,
        sender_name: str,
        message_text: str,
        timestamp: datetime,
        context_messages: Iterable[ContextMessage],
        candidate_languages: Sequence[str],
        *,
        allow_same_language: bool = False,
    ) -> List[TranslationResult]:
        # 絵文字や数字だけは言語判定が暴れるので、翻訳自体をしない
        if not allow_same_language and not self._language_detector.has_translatable_text(message_text):
            logger.info("Skip translation; message has no linguistic content")
            return []

        # 混在文はどの言語でも「全文がその言語」ではないので、グループの全言語へ訳す
        mixed = not allow_same_language and self._language_detector.is_mixed_language(message_text)
        if allow_same_language or mixed:
            detected_lang = ""
            filtered_targets = [lang for lang in candidate_languages if lang]
            if mixed:
                logger.info(
                    "Mixed-language message; keep every group language",
                    extra={"targets": list(filtered_targets)},
                )
        else:
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
