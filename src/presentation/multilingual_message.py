from __future__ import annotations

import logging
from typing import List, Sequence

from ..domain.services.interface_translation_service import InterfaceTranslationService
from .reply_formatter import strip_source_echo


def dedup_lang_codes(languages: Sequence[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for code in languages:
        lowered = (code or "").lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(lowered)
    return deduped


def build_multilingual_message(
    *,
    base_text: str,
    languages: Sequence[str],
    translator: InterfaceTranslationService | None,
    logger: logging.Logger,
    warning_log: str,
) -> str:
    trimmed = (base_text or "").strip()
    if not trimmed:
        return ""

    normalized_languages = dedup_lang_codes(languages)
    if not normalized_languages or not translator:
        return trimmed

    target_langs = [lang for lang in normalized_languages if not lang.startswith("en")]
    text_by_lang = {}
    if target_langs:
        try:
            translations = translator.translate(trimmed, target_langs)
            for item in translations or []:
                lowered = (item.lang or "").lower()
                if not lowered or lowered in text_by_lang:
                    continue
                cleaned = strip_source_echo(trimmed, item.text)
                normalized = (cleaned or item.text or "").strip()
                if not normalized:
                    continue
                text_by_lang[lowered] = normalized
        except Exception:  # pylint: disable=broad-except
            logger.warning(warning_log, exc_info=True)

    lines: List[str] = [trimmed]
    for lang in normalized_languages:
        if lang.startswith("en"):
            continue
        translated = text_by_lang.get(lang)
        if not translated or translated in lines:
            continue
        lines.append(translated)

    return "\n\n".join(lines).strip()[:5000]
