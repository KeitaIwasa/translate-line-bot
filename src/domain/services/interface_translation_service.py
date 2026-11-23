from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Sequence

from ..models import TranslationRequest, TranslationResult
from ..ports import TranslationPort
from .notice_tokens import fallback_notice_text, notice_tokens_preserved

logger = logging.getLogger(__name__)


class InterfaceTranslationService:
    """UI 系の定型文を各設定言語へ翻訳するためのユーティリティ。"""

    def __init__(self, translator: TranslationPort) -> None:
        self._translator = translator

    def translate(self, base_text: str, target_languages: Sequence[str]) -> List[TranslationResult]:
        unique_targets: List[str] = []
        seen = set()
        for lang in target_languages:
            if not lang:
                continue
            lowered = lang.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            unique_targets.append(lang)

        if not base_text or not unique_targets:
            return []

        first = self._translate_request(base_text, unique_targets)
        if _results_preserve_tokens(base_text, first):
            return first
        logger.info("Retrying system notice translation because a number or date changed")
        second = self._translate_request(base_text, unique_targets)
        chosen = second or first
        return [_coerce_notice_result(base_text, item) for item in chosen]

    def translate_many(self, texts: Sequence[str], target_language: str) -> List[str]:
        """プランメニューなどの複数文言を1言語へまとめて翻訳する。"""

        originals = [str(text) for text in texts]
        if not originals or not (target_language or "").strip():
            return originals
        batch = getattr(self._translator, "translate_ui_strings", None)
        if batch is None:
            raise RuntimeError("translator does not support batched UI translation")
        translated = _normalize_batch(batch(originals, target_language), originals)
        if _batch_preserves_tokens(originals, translated):
            return translated
        logger.info("Retrying system notice batch translation because a number or date changed")
        retried = _normalize_batch(batch(originals, target_language), originals)
        chosen = retried if _batch_preserves_tokens(originals, retried) or len(retried) == len(originals) else translated
        return [
            item if notice_tokens_preserved(source, item) else fallback_notice_text(source, target_language)
            for source, item in zip(originals, chosen)
        ]

    def _translate_request(self, base_text: str, target_languages: Sequence[str]) -> List[TranslationResult]:
        request = TranslationRequest(
            sender_name="System",
            message_text=base_text,
            timestamp=datetime.now(timezone.utc),
            candidate_languages=list(target_languages),
            context_messages=[],
        )
        return self._translator.translate(request)


def _results_preserve_tokens(source: str, results: Sequence[TranslationResult]) -> bool:
    if not results:
        return True
    return all(notice_tokens_preserved(source, item.text or "") for item in results)


def _coerce_notice_result(source: str, item: TranslationResult) -> TranslationResult:
    if notice_tokens_preserved(source, item.text or ""):
        return item
    return TranslationResult(lang=item.lang, text=fallback_notice_text(source, item.lang))


def _normalize_batch(translated, originals: Sequence[str]) -> List[str]:
    if not isinstance(translated, list) or len(translated) != len(originals):
        return list(originals)
    return [
        item if isinstance(item, str) and item.strip() else original
        for item, original in zip(translated, originals)
    ]


def _batch_preserves_tokens(originals: Sequence[str], translated: Sequence[str]) -> bool:
    return all(notice_tokens_preserved(source, item) for source, item in zip(originals, translated))
