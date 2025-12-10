from __future__ import annotations

from langdetect import LangDetectException, detect, DetectorFactory

# 乱数シードを固定して言語判定結果のブレを防ぐ
DetectorFactory.seed = 0


class LanguageDetectionService:
    """メッセージ言語を判定するための薄いユーティリティ。"""

    def detect(self, text: str) -> str:
        if not text:
            return ""
        try:
            return detect(text)
        except LangDetectException:
            return ""
