from __future__ import annotations

import re
from typing import Sequence

from .language_names import localized_language_name

# 言語の追加・削除ではなく、「今どの言語か」を聞いているときの定型応答。
# current / set / now / 今 / 現在 / 設定 が無い「どの言語」「which languages」は対応言語の質問なので含めない
_JA_QUESTION = re.compile(
    r"(?:"
    r"(?:今|現在).{0,8}(?:翻訳)?言語.{0,8}(?:何|なに|なん|どれ|教えて|見せて|確認)"
    r"|(?:言語設定|設定している言語|設定中の言語).{0,10}(?:何|なに|なん|どれ|教えて|見せて|確認|一覧)"
    r"|(?:今|現在|設定).{0,10}どの言語"
    r"|どの言語.{0,16}(?:今|現在|設定)"
    r")"
)
_JA_MUTATION = re.compile(r"追加|削除|消して|外して|リセット|初期化|変更|変えて|にして|止めて|停止|再開|抜いて|入れて")
# 対応言語の案内は現在設定の質問にしない
_JA_SUPPORT = re.compile(r"対応")
_EN_STATUS = re.compile(r"\b(?:current(?:ly)?|now|set)\b", re.IGNORECASE)
_EN_QUESTION = re.compile(
    r"(?:"
    r"\b(?:what|which|show|list)\b.{0,60}\blanguages?\b"
    r"|\bcurrent(?:ly)?\b.{0,40}\blanguages?\b"
    r"|\blanguages?\b.{0,40}\b(?:current(?:ly)?|set|now)\b"
    r")",
    re.IGNORECASE,
)
_EN_SUPPORT = re.compile(r"\bsupports?\b", re.IGNORECASE)
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



def is_current_languages_question(text: str) -> bool:
    """現在の翻訳言語を尋ねていて、変更は求めていないとき True。"""

    stripped = (text or "").strip()
    if not stripped:
        return False
    if (
        _JA_QUESTION.search(stripped)
        and not _JA_MUTATION.search(stripped)
        and not _JA_SUPPORT.search(stripped)
    ):
        return True
    if (
        _EN_QUESTION.search(stripped)
        and _EN_STATUS.search(stripped)
        and not _EN_SUPPORT.search(stripped)
        and not _english_requests_change(stripped)
    ):
        return True
    return False


def _english_requests_change(text: str) -> bool:
    """言語の追加・変更を求めているとき True。状態の set は変更にしない。"""

    if _EN_MUTATION.search(text):
        return True
    without_status = _EN_SET_STATUS.sub(" ", text)
    return _EN_SET.search(without_status) is not None


def current_languages_base_text(languages: Sequence) -> str:
    """設定中の言語を英語の1文で返す。未設定ならその旨。コードは出さない。"""

    return _current_languages_text(languages, "en")


def current_languages_localized_text(languages: Sequence, reply_language: str) -> str | None:
    """ja/en/zh/ko/th は固定の名前表で一覧を返す。それ以外は None。"""

    family = _deterministic_family(reply_language)
    if family is None:
        return None
    return _current_languages_text(languages, family)


def _deterministic_family(reply_language: str) -> str | None:
    lowered = (reply_language or "").strip().lower().replace("_", "-")
    if not lowered or lowered.startswith("en"):
        return "en"
    for prefix in ("ja", "zh", "ko", "th"):
        if lowered.startswith(prefix):
            return prefix
    return None


def _current_languages_text(languages: Sequence, family: str) -> str:
    names = _language_names(languages, family)
    if not names:
        return {
            "ja": "翻訳言語はまだ設定されていません。",
            "zh": "还没有设置翻译语言。",
            "ko": "아직 번역 언어가 없어.",
            "th": "ยังไม่ได้ตั้งภาษาแปล",
        }.get(family, "No translation languages are set yet.")
    if family == "ja":
        return "現在の翻訳言語は" + "、".join(names) + "です。"
    if family == "zh":
        return "当前的翻译语言是" + "、".join(names) + "。"
    if family == "ko":
        return "현재 번역 언어는 " + ", ".join(names) + "야."
    if family == "th":
        return "ภาษาแปลตอนนี้คือ " + ", ".join(names)
    if len(names) == 1:
        return f"The current translation language is {names[0]}."
    return "The current translation languages are " + ", ".join(names) + "."


def _language_names(languages: Sequence, family: str) -> list[str]:
    """コード、または (コード, 保存名) から表示名を作る。"""

    names: list[str] = []
    seen: set[str] = set()
    for code, stored_name in _language_records(languages):
        if code in seen:
            continue
        seen.add(code)
        names.append(localized_language_name(code, family, stored_name))
    return names


def _language_records(languages: Sequence) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for item in languages or []:
        code, stored_name = _record_parts(item)
        normalized = (code or "").strip().lower().replace("_", "-")
        if not normalized:
            continue
        records.append((normalized, stored_name))
    return records


def _record_parts(item) -> tuple[str, str]:
    if isinstance(item, str):
        return item, ""
    code = getattr(item, "code", None)
    name = getattr(item, "name", None)
    if isinstance(code, str):
        return code, name if isinstance(name, str) else ""
    if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
        stored = item[1] if len(item) > 1 and isinstance(item[1], str) else ""
        return str(item[0] or ""), stored
    return "", ""
