"""システム案内の翻訳で、数字と日付が原文から変わっていないかを見る。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date

from .language_mention import ui_family

# 英語原文そのものをキーにする。ja/zh/ko/th の固定訳が無い文は英語へ戻す。
FIXED_NOTICE_TRANSLATIONS: dict[str, dict[str, str]] = {}

_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_NUMBER = re.compile(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+)(?!\d)")
_MONTH_NAMES = {
    1: ("january", "jan"),
    2: ("february", "feb"),
    3: ("march", "mar"),
    4: ("april", "apr"),
    5: ("may",),
    6: ("june", "jun"),
    7: ("july", "jul"),
    8: ("august", "aug"),
    9: ("september", "sep", "sept"),
    10: ("october", "oct"),
    11: ("november", "nov"),
    12: ("december", "dec"),
}


def notice_tokens_preserved(source: str, translated: str) -> bool:
    """原文の数字と日付が、訳文に同じ値で残っているか。

    全角数字は半角に揃える。日付は ISO のままか、同じ日付に戻せる表記だけを認める。
    50 が 5万 や 50,000 になっていたら不一致。
    """

    source_norm = _normalize(source)
    translated_norm = _normalize(translated)
    dates = _source_dates(source_norm)
    for item in dates:
        if not _translation_has_date(translated_norm, item):
            return False
    needed = Counter(_number_values(_without_iso_dates(source_norm)))
    if not needed:
        return True
    found = Counter(_number_values(_without_dates(translated_norm, dates)))
    return all(found[value] >= count for value, count in needed.items())


def fallback_notice_text(source: str, lang: str) -> str:
    """数字か日付が変わった訳の代わり。固定の ja/zh/ko/th があればそれ、無ければ英語。"""

    family = ui_family(lang)
    fixed = FIXED_NOTICE_TRANSLATIONS.get(source, {}).get(family)
    if fixed:
        return fixed
    return source


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return normalized.replace("–", "-").replace("—", "-")


def _source_dates(text: str) -> list[date]:
    found: list[date] = []
    for match in _ISO_DATE.finditer(text):
        try:
            found.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return found


def _without_iso_dates(text: str) -> str:
    return _ISO_DATE.sub(" ", text)


def _number_values(text: str) -> list[int]:
    return [int(match.group(0).replace(",", "")) for match in _NUMBER.finditer(text)]


def _date_patterns(target: date) -> list[re.Pattern[str]]:
    year, month, day = target.year, target.month, target.day
    raw = [
        re.escape(target.isoformat()),
        rf"{year}[-/.]0?{month}[-/.]0?{day}(?!\d)",
        rf"{year}\s*年\s*0?{month}\s*月\s*0?{day}\s*日",
        rf"{year}\s*년\s*0?{month}\s*월\s*0?{day}\s*일",
    ]
    for name in _MONTH_NAMES[month]:
        raw.append(rf"{name}\s+0?{day}(?:st|nd|rd|th)?[,]?\s+{year}")
        raw.append(rf"0?{day}(?:st|nd|rd|th)?\s+{name}[,]?\s+{year}")
    return [re.compile(pattern, re.IGNORECASE) for pattern in raw]


def _translation_has_date(text: str, target: date) -> bool:
    return any(pattern.search(text) for pattern in _date_patterns(target))


def _without_dates(text: str, targets: list[date]) -> str:
    stripped = text
    for target in targets:
        for pattern in _date_patterns(target):
            stripped = pattern.sub(" ", stripped)
    return stripped
