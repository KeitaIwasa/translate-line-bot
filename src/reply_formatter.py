from __future__ import annotations

from typing import List

from .translator.gemini_client import Translation

MAX_REPLY_LENGTH = 5000


def format_translations(translations: List[Translation]) -> str:
    lines: List[str] = []
    for item in translations:
        text = (item.text or "").strip()
        if text:
            lines.append(text)
    joined = "\n\n".join(lines)
    return joined[:MAX_REPLY_LENGTH]
