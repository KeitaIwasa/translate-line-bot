from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Sequence

from ..models import TranslationRequest, TranslationResult
from ..ports import TranslationPort


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

        request = TranslationRequest(
            sender_name="System",
            message_text=base_text,
            timestamp=datetime.now(timezone.utc),
            candidate_languages=unique_targets,
            context_messages=[],
        )

        return self._translator.translate(request)

    def translate_many(self, texts: Sequence[str], target_language: str) -> List[str]:
        """プランメニューなどの複数文言を1言語へまとめて翻訳する。"""

        originals = [str(text) for text in texts]
        if not originals or not (target_language or "").strip():
            return originals
        batch = getattr(self._translator, "translate_ui_strings", None)
        if batch is None:
            raise RuntimeError("translator does not support batched UI translation")
        translated = batch(originals, target_language)
        if not isinstance(translated, list) or len(translated) != len(originals):
            return originals
        return [
            item if isinstance(item, str) and item.strip() else original
            for item, original in zip(translated, originals)
        ]
