from __future__ import annotations

import re
from typing import List

from ..domain.models import TranslationResult

MAX_REPLY_LENGTH = 5000


def strip_source_echo(source_text: str, translated_text: str) -> str:
    """Gemini が原文をエコーした部分を除去するユーティリティ。"""

    if not source_text or not translated_text:
        return translated_text or ""

    source = source_text.strip()
    candidate = translated_text.strip()

    # 完全一致
    if candidate.lower() == source.lower():
        return ""

    # "<source> - <translation>" などのパターン
    prefix_pattern = rf"^{re.escape(source)}\s*[-:：、，,。\u3000]*"
    candidate = re.sub(prefix_pattern, "", candidate, flags=re.IGNORECASE)

    # "(<translation>)" 形式
    if candidate.startswith(source):
        candidate = candidate[len(source):].lstrip(" ()[]-—–:：、，,。\u3000")

    return candidate.strip()


def format_translations(translations: List[TranslationResult]) -> str:
    lines: List[str] = []
    for item in translations:
        text = (item.text or "").strip()
        if text:
            lines.append(text)
    joined = "\n\n".join(lines)
    return joined[:MAX_REPLY_LENGTH]


def build_translation_reply(original_text: str, translations: List[TranslationResult]) -> str:
    cleaned = [
        TranslationResult(lang=item.lang, text=strip_source_echo(original_text, item.text))
        for item in translations
    ]
    return format_translations(cleaned)
