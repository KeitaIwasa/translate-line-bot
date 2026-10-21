from __future__ import annotations

import unicodedata
from typing import Mapping, Sequence

from langdetect import DetectorFactory, LangDetectException, detect

# 乱数シードを固定して言語判定結果のブレを防ぐ
DetectorFactory.seed = 0

# ラテン文字を使う言語。漢字だけの文を ko と誤検知したあと、ここへ落とさない
_LATIN_LANGUAGES = frozenset(
    {
        "en", "fr", "de", "es", "pt", "it", "nl", "sv", "pl", "id", "vi", "tr",
        "af", "tl", "ro", "hu", "cs", "da", "fi", "no", "nb", "nn", "ca", "sk",
        "hr", "sl", "et", "lt", "lv", "sq", "sw", "so", "ms", "cy", "ga", "eu",
    }
)
# これより短いラテン文の langdetect 結果は、グループ言語に含まれない限り採用しない
_MIN_LATIN_LETTERS = 12

_EMPTY_SIGNALS = {
    "kana": 0,
    "hangul": 0,
    "han": 0,
    "latin": 0,
    "cyrillic": 0,
    "arabic": 0,
    "thai": 0,
    "devanagari": 0,
    "hebrew": 0,
    "greek": 0,
}


class LanguageDetectionService:
    """メッセージ言語を判定する。

    detect() は langdetect の生の結果を返す。返信言語は resolve_instruction_language() を使い、
    文字種とグループ言語に合わないコード（漢字の多い日本語を ko と誤検知するなど）は捨てる。
    """

    def detect(self, text: str) -> str:
        if not text:
            return ""
        try:
            return detect(text)
        except LangDetectException:
            return ""

    def resolve_instruction_language(
        self,
        text: str,
        *,
        preferred: str = "",
        group_languages: Sequence[str] = (),
        detected: str | None = None,
    ) -> str:
        """返信に使う言語コードを返す。判定できないときはグループ言語、それも無ければ空。"""

        signals = _script_signals(text or "")
        group = _normalize_group(group_languages)
        preferred_code = _normalize_code(preferred)
        if preferred_code and _language_fits(preferred_code, signals):
            return preferred_code

        detected_code = _normalize_code(self.detect(text) if detected is None else detected)
        if _accept_detected(detected_code, signals, group):
            return detected_code

        inferred = _infer_from_signals(signals, group)
        if inferred:
            return inferred
        return group[0] if group else ""


def _normalize_group(languages: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for code in languages:
        lowered = _normalize_code(code)
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(lowered)
    return normalized


def _normalize_code(code: str) -> str:
    lowered = (code or "").strip().lower().replace("_", "-")
    if not lowered:
        return ""
    if lowered.startswith("zh"):
        if "tw" in lowered or "hant" in lowered:
            return "zh-tw"
        if lowered == "zh":
            return "zh"
        return "zh-cn"
    return lowered.split("-", 1)[0]


def _script_signals(text: str) -> Mapping[str, int]:
    counts = dict(_EMPTY_SIGNALS)
    for char in text:
        if char == "\ufffd" or unicodedata.category(char).startswith("C"):
            continue
        codepoint = ord(char)
        # 表示名が欠けると混ざる IPA（例: ʖ）。言語の根拠にしない
        if 0x0250 <= codepoint <= 0x02AF:
            continue
        if (
            0x3040 <= codepoint <= 0x30FF
            or 0x31F0 <= codepoint <= 0x31FF
            or 0xFF66 <= codepoint <= 0xFF9D
        ):
            counts["kana"] += 1
        elif 0xAC00 <= codepoint <= 0xD7AF or 0x1100 <= codepoint <= 0x11FF:
            counts["hangul"] += 1
        elif 0x4E00 <= codepoint <= 0x9FFF or 0x3400 <= codepoint <= 0x4DBF:
            counts["han"] += 1
        elif _is_latin_letter(char, codepoint):
            counts["latin"] += 1
        elif 0x0400 <= codepoint <= 0x04FF:
            counts["cyrillic"] += 1
        elif 0x0600 <= codepoint <= 0x06FF or 0x0750 <= codepoint <= 0x077F:
            counts["arabic"] += 1
        elif 0x0E00 <= codepoint <= 0x0E7F:
            counts["thai"] += 1
        elif 0x0900 <= codepoint <= 0x097F:
            counts["devanagari"] += 1
        elif 0x0590 <= codepoint <= 0x05FF:
            counts["hebrew"] += 1
        elif 0x0370 <= codepoint <= 0x03FF:
            counts["greek"] += 1
    return counts


def _is_latin_letter(char: str, codepoint: int) -> bool:
    if ("A" <= char <= "Z") or ("a" <= char <= "z") or 0xFF21 <= codepoint <= 0xFF3A or 0xFF41 <= codepoint <= 0xFF5A:
        return True
    if 0x00C0 <= codepoint <= 0x024F and unicodedata.category(char).startswith("L"):
        return True
    return False


def _language_fits(code: str, signals: Mapping[str, int]) -> bool:
    """そのコードが本文の文字種で説明できるときだけ True。漢字だけでは韓国語にしない。"""

    kana = signals["kana"]
    hangul = signals["hangul"]
    han = signals["han"]
    if code == "ja":
        if hangul > kana:
            return False
        return kana > 0 or (han > 0 and hangul == 0)
    if code == "ko":
        return hangul > 0 and hangul >= kana
    if code.startswith("zh"):
        return han > 0 and kana == 0 and hangul == 0
    if code in _LATIN_LANGUAGES:
        return signals["latin"] > 0 and kana == 0 and hangul == 0 and han == 0 and not _other_script(signals)
    if code == "ar":
        return signals["arabic"] > 0
    if code in {"ru", "uk", "bg", "sr", "mk", "be"}:
        return signals["cyrillic"] > 0
    if code == "th":
        return signals["thai"] > 0
    if code == "hi":
        return signals["devanagari"] > 0
    if code == "he":
        return signals["hebrew"] > 0
    if code == "el":
        return signals["greek"] > 0
    return False


def _other_script(signals: Mapping[str, int]) -> bool:
    return any(signals[name] > 0 for name in ("cyrillic", "arabic", "thai", "devanagari", "hebrew", "greek"))


def _is_decisive(code: str, signals: Mapping[str, int]) -> bool:
    """文字種だけで言語が決まる場合。漢字のみは日本語と中国語の両方があり得る。"""

    if code == "ja" and signals["kana"] > 0 and signals["kana"] >= signals["hangul"]:
        return True
    if code == "ko" and signals["hangul"] > 0 and signals["hangul"] > signals["kana"]:
        return True
    if code == "ar" and signals["arabic"] > 0 and signals["arabic"] >= signals["latin"]:
        return True
    if code == "th" and signals["thai"] > 0:
        return True
    if code == "hi" and signals["devanagari"] > 0:
        return True
    if code == "he" and signals["hebrew"] > 0:
        return True
    if code == "el" and signals["greek"] > 0:
        return True
    if code in {"ru", "uk", "bg"} and signals["cyrillic"] > 0 and signals["latin"] == 0:
        return True
    return False


def _accept_detected(code: str, signals: Mapping[str, int], group: Sequence[str]) -> bool:
    if not code or not _language_fits(code, signals):
        return False
    if _is_decisive(code, signals) or code in group:
        return True
    # 十分な長さのラテン文だけ、グループ外の言語（フランス語など）を本文の言語として採用する
    return code in _LATIN_LANGUAGES and signals["latin"] >= _MIN_LATIN_LETTERS


def _infer_from_signals(signals: Mapping[str, int], group: Sequence[str]) -> str:
    if signals["kana"] > 0 and signals["kana"] >= signals["hangul"]:
        return "ja"
    if signals["hangul"] > 0:
        return "ko"
    if signals["han"] > 0:
        for code in group:
            if code == "ja" or code.startswith("zh"):
                return code
        return "ja"
    if signals["arabic"] > 0:
        return "ar"
    if signals["cyrillic"] > 0:
        return "ru"
    if signals["thai"] > 0:
        return "th"
    if signals["devanagari"] > 0:
        return "hi"
    if signals["hebrew"] > 0:
        return "he"
    if signals["greek"] > 0:
        return "el"
    if signals["latin"] > 0:
        if "en" in group:
            return "en"
        for code in group:
            if code in _LATIN_LANGUAGES:
                return code
        return "en"
    return ""
