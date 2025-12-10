from __future__ import annotations

import re
from typing import List, Sequence

from ..domain.models import TranslationResult

MAX_REPLY_LENGTH = 5000

# RTL 系言語コードのプレフィックス（先頭一致で判定）
RTL_LANG_PREFIXES: Sequence[str] = (
    "ar",  # Arabic
    "he",  # Hebrew
    "fa",  # Persian
    "ur",  # Urdu
    "ps",  # Pashto
    "ku",  # Kurdish (Sorani 等)
    "sd",  # Sindhi
    "ug",  # Uyghur
    "yi",  # Yiddish
)


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


def _wrap_bidi_isolate(text: str, lang: str) -> str:
    """行単位で双方向テキストを安定させるラッパー。

    LINE クライアントでは isolate (LRI/RLI) が効かない場合があるため、
    より互換性の高い embedding (LRE/RLE) に切り替える。
    LTR 行末には LRM、RTL 行末には RLM を付け、句読点が隣行の
    方向性に引っ張られるのを防ぐ。
    """

    if not text:
        return text

    lowered = (lang or "").lower()
    if lowered.startswith(RTL_LANG_PREFIXES):
        # 前に RLM、行全体を RLE ... PDF で囲み、末尾にも RLM を付与
        return f"\u200F\u202B{text}\u202C\u200F"

    # LTR: 前に LRM、LRE ... PDF で囲み、末尾にも LRM を付与
    return f"\u200E\u202A{text}\u202C\u200E"


def format_translations(translations: List[TranslationResult]) -> str:
    lines: List[str] = []
    for item in translations:
        text = (item.text or "").strip()
        if text:
            lines.append(_wrap_bidi_isolate(text, item.lang))
    joined = "\n\n".join(lines)
    return joined[:MAX_REPLY_LENGTH]


def build_translation_reply(original_text: str, translations: List[TranslationResult]) -> str:
    cleaned = [
        TranslationResult(lang=item.lang, text=strip_source_echo(original_text, item.text))
        for item in translations
    ]
    return format_translations(cleaned)
