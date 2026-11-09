"""システム案内の翻訳で、数字と日付が原文から変わっていないかを見る。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from datetime import date

from ...presentation.reply_formatter import strip_source_echo
from .language_mention import ui_family

# 英語原文そのものをキーにする。ja/zh/ko/th の固定訳が無い文は英語へ戻す。
FIXED_NOTICE_TRANSLATIONS: dict[str, dict[str, str]] = {}

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
    """送信する文の数字と日付が、原文と過不足なく同じか。

    判定は echo 除去のあと、呼び出し側が実際に送る文字列に対して行う。
    全角数字は半角に揃える。日付は ISO のままか、同じ日付に戻せる表記だけを認める。
    足りない値も、余分な値も不一致。50 が 5万 や 50,000 になっていたら不一致。
    """

    return _token_signature(_normalize(source)) == _token_signature(_delivered_notice_text(source, translated))


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


def _delivered_notice_text(source: str, translated: str) -> str:
    """strip_source_echo 後に送られる文。完全なエコーは原文そのものを送る。"""

    cleaned = strip_source_echo(source or "", translated or "")
    return _normalize(cleaned or translated or "")


def _number_values(text: str) -> list[int]:
    return [int(match.group(0).replace(",", "")) for match in _NUMBER.finditer(text)]


_MONTH_INDEX = {name: month for month, names in _MONTH_NAMES.items() for name in names}
_MONTH_NAME_PATTERN = "|".join(sorted(_MONTH_INDEX, key=len, reverse=True))
_YEAR_FIRST_DATE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_JA_DATE = re.compile(r"(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_KO_DATE = re.compile(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_EN_MONTH_DAY = re.compile(
    rf"\b({_MONTH_NAME_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(\d{{4}})\b",
    re.IGNORECASE,
)
_EN_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAME_PATTERN})[,]?\s+(\d{{4}})\b",
    re.IGNORECASE,
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_spans(text: str) -> list[tuple[int, int, date]]:
    """同じ日付に戻せる表記だけを拾う。11/01/2026 のような月日の曖昧な並びは含めない。"""

    found: list[tuple[int, int, date]] = []

    def add(match: re.Match[str], parsed: date | None) -> None:
        if parsed is not None:
            found.append((match.start(), match.end(), parsed))

    for match in _YEAR_FIRST_DATE.finditer(text):
        add(match, _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in _JA_DATE.finditer(text):
        add(match, _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in _KO_DATE.finditer(text):
        add(match, _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in _EN_MONTH_DAY.finditer(text):
        add(match, _safe_date(int(match.group(3)), _MONTH_INDEX[match.group(1).lower()], int(match.group(2))))
    for match in _EN_DAY_MONTH.finditer(text):
        add(match, _safe_date(int(match.group(3)), _MONTH_INDEX[match.group(2).lower()], int(match.group(1))))

    found.sort(key=lambda item: (item[0], item[0] - item[1]))
    chosen: list[tuple[int, int, date]] = []
    cursor = -1
    for start, end, parsed in found:
        if start < cursor:
            continue
        chosen.append((start, end, parsed))
        cursor = end
    return chosen


def _token_signature(text: str) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[int, int], ...]]:
    spans = _date_spans(text)
    masked = list(text)
    for start, end, _parsed in spans:
        for index in range(start, end):
            masked[index] = " "
    dates = Counter(item.isoformat() for _start, _end, item in spans)
    numbers = Counter(_number_values("".join(masked)))
    return (tuple(sorted(dates.items())), tuple(sorted(numbers.items())))
