from __future__ import annotations

import re
from typing import Sequence

# 言語の追加・削除ではなく、「今どの言語か」を聞いているときの定型応答
_JA_QUESTION = re.compile(
    r"(?:"
    r"(?:今|現在).{0,8}(?:翻訳)?言語.{0,6}(?:何|なに|なん|どれ)"
    r"|翻訳言語.{0,8}(?:は|って|を)?(?:何|なに|なん|どれ|教えて|見せて|確認)"
    r"|どの言語"
    r"|(?:言語設定|設定している言語|設定中の言語).{0,10}(?:何|教えて|見せて|確認|一覧)"
    r")"
)
_JA_MUTATION = re.compile(r"追加|削除|消して|外して|リセット|初期化|変更|変えて|にして|止めて|停止|再開|抜いて|入れて")
_EN_QUESTION = re.compile(
    r"(?:"
    r"\b(?:what|which)\b.{0,40}\blanguages?\b"
    r"|\bcurrent\b.{0,30}\blanguages?\b"
    r"|\b(?:show|list)\b.{0,30}\blanguages?\b"
    r")",
    re.IGNORECASE,
)
_EN_MUTATION = re.compile(
    r"\b(?:add|remove|change|reset|switch|stop|pause|delete)\b",
    re.IGNORECASE,
)
# 「set English」は変更。「are set」「currently set」「set now」「is set」は状態の質問
_EN_SET = re.compile(r"\bset\b", re.IGNORECASE)
_EN_SET_STATUS = re.compile(
    r"\b(?:are|is)\s+set\b|\bcurrently\s+set\b|\bset\s+now\b",
    re.IGNORECASE,
)

# ベース原文は英語。呼び出し側がグループの返信言語へ翻訳する
_LANGUAGE_NAMES = {
    "ja": "Japanese",
    "en": "English",
    "ko": "Korean",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-tw": "Traditional Chinese",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
}


def is_current_languages_question(text: str) -> bool:
    """現在の翻訳言語を尋ねていて、変更は求めていないとき True。"""

    stripped = (text or "").strip()
    if not stripped:
        return False
    if _JA_QUESTION.search(stripped) and not _JA_MUTATION.search(stripped):
        return True
    if _EN_QUESTION.search(stripped) and not _english_requests_change(stripped):
        return True
    return False


def _english_requests_change(text: str) -> bool:
    """言語の追加・変更を求めているとき True。状態の set は変更にしない。"""

    if _EN_MUTATION.search(text):
        return True
    without_status = _EN_SET_STATUS.sub(" ", text)
    return _EN_SET.search(without_status) is not None


def current_languages_base_text(language_codes: Sequence[str]) -> str:
    """設定中の言語を英語の1文で返す。未設定ならその旨。"""

    names: list[str] = []
    seen: set[str] = set()
    for code in language_codes:
        lowered = (code or "").strip().lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        names.append(_LANGUAGE_NAMES.get(lowered, lowered))
    if not names:
        return "No translation languages are set yet."
    if len(names) == 1:
        return f"The current translation language is {names[0]}."
    return "The current translation languages are " + ", ".join(names) + "."
