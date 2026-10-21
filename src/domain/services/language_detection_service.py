from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from langdetect import LangDetectException, detect, detect_langs, DetectorFactory

# 乱数シードを固定して言語判定結果のブレを防ぐ
DetectorFactory.seed = 0

# これ未満は誤判定が多い。グループ言語か文字体系へ倒す
_LOW_CONFIDENCE = 0.8

# 漢字だけの短い文は ja/zh/ko を取り違える
_CJK_LANGS = {"ja", "zh", "ko"}

# ラテン文字のグループ言語。低信頼のとき、候補が1つならそれを使う
_LATIN_LANGS = {
    "en", "fr", "de", "es", "it", "pt", "nl", "id", "vi", "pl",
    "sv", "da", "fi", "cs", "ro", "hu", "tr", "af", "ca", "ms",
}


class LanguageDetectionService:
    """メッセージ言語を判定するための薄いユーティリティ。"""

    def detect(self, text: str) -> str:
        if not text:
            return ""
        try:
            return detect(text)
        except LangDetectException:
            return ""

    def resolve_reply_language(
        self,
        text: str,
        *,
        group_languages: Sequence[str] | None = None,
        bot_mention_name: str = "",
    ) -> str:
        """返信言語を決める。

        メンションとボット名を除いた本文で判定する。信頼度が低い、または文字体系と
        判定が食い違うときは、グループの言語か本文の文字体系を優先する。
        絵文字だけ・数字だけは空文字（呼び出し側は英語のベース原文を使う）。
        """
        stripped = _strip_bot_mention(text, bot_mention_name)
        if not _has_linguistic_chars(stripped):
            return ""

        group = _primary_codes(group_languages or [])
        hint = _script_hint(stripped)
        top, prob = _top_language(self._probabilities(stripped))

        if hint in {"ja", "ko", "th", "ar", "ru", "he", "el"}:
            return hint

        if hint == "han":
            return _choose_han(top, prob, group)

        if prob >= _LOW_CONFIDENCE and top:
            return top

        if top and top in group:
            return top
        if hint == "latin":
            latin_group = [code for code in group if code in _LATIN_LANGS]
            if len(latin_group) == 1:
                return latin_group[0]
        if len(group) == 1:
            return group[0]
        return top

    def _probabilities(self, text: str) -> List[Tuple[str, float]]:
        try:
            return [(_primary(item.lang), float(item.prob)) for item in detect_langs(text)]
        except LangDetectException:
            return []


def _strip_bot_mention(text: str, bot_mention_name: str) -> str:
    """表示名が文字化けしていても、設定されたボット名の範囲を本文から外す。"""
    stripped = text or ""
    name = (bot_mention_name or "").strip()
    if name:
        stripped = re.sub(rf"@?\s*{re.escape(name)}", " ", stripped, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", stripped).strip()


def _primary(code: str) -> str:
    return (code or "").strip().lower().replace("_", "-").split("-")[0]


def _primary_codes(codes: Iterable[str]) -> List[str]:
    seen = []
    for code in codes:
        primary = _primary(code)
        if primary and primary not in seen:
            seen.append(primary)
    return seen


def _has_linguistic_chars(text: str) -> bool:
    return bool(re.search(r"[^\W\d_]", text, flags=re.UNICODE))


def _script_hint(text: str) -> str:
    """文字体系から言語を決める。漢字だけは han、混在は mixed、ラテンだけは latin。"""
    counts = {
        "kana": 0,
        "hangul": 0,
        "han": 0,
        "thai": 0,
        "arabic": 0,
        "hebrew": 0,
        "cyrillic": 0,
        "greek": 0,
        "latin": 0,
    }
    for char in text:
        code = ord(char)
        if 0x3040 <= code <= 0x30FF:
            counts["kana"] += 1
        elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
            counts["hangul"] += 1
        elif 0x4E00 <= code <= 0x9FFF:
            counts["han"] += 1
        elif 0x0E00 <= code <= 0x0E7F:
            counts["thai"] += 1
        elif 0x0600 <= code <= 0x06FF:
            counts["arabic"] += 1
        elif 0x0590 <= code <= 0x05FF:
            counts["hebrew"] += 1
        elif 0x0400 <= code <= 0x04FF:
            counts["cyrillic"] += 1
        elif 0x0370 <= code <= 0x03FF:
            counts["greek"] += 1
        elif ("A" <= char <= "Z") or ("a" <= char <= "z"):
            counts["latin"] += 1

    kana = counts["kana"]
    hangul = counts["hangul"]
    if kana and not hangul:
        return "ja"
    if hangul and not kana:
        return "ko"
    if kana and hangul:
        return "mixed"
    for key, lang in (
        ("thai", "th"),
        ("arabic", "ar"),
        ("hebrew", "he"),
        ("cyrillic", "ru"),
        ("greek", "el"),
    ):
        if counts[key]:
            return lang
    if counts["han"] and not counts["latin"]:
        return "han"
    if counts["latin"] and counts["han"]:
        return "mixed"
    if counts["latin"]:
        return "latin"
    return ""


def _top_language(probabilities: Sequence[Tuple[str, float]]) -> Tuple[str, float]:
    if not probabilities:
        return "", 0.0
    lang, prob = max(probabilities, key=lambda item: item[1])
    return lang, prob


def _choose_han(top: str, prob: float, group: Sequence[str]) -> str:
    """漢字だけのときはグループの CJK 言語を優先し、なければ信頼できる判定を使う。"""
    cjk_group = [code for code in group if code in _CJK_LANGS]
    if top in cjk_group and prob >= _LOW_CONFIDENCE:
        return top
    if "ja" in cjk_group:
        return "ja"
    if len(cjk_group) == 1:
        return cjk_group[0]
    if top in _CJK_LANGS:
        return top
    return cjk_group[0] if cjk_group else top
