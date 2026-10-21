from __future__ import annotations

import re
import unicodedata

from langdetect import LangDetectException, detect, DetectorFactory

# 乱数シードを固定して言語判定結果のブレを防ぐ
DetectorFactory.seed = 0

# 数字に付く通貨・単位の文字。これしか文字が無いメッセージは翻訳しない。
_CURRENCY_LETTERS = frozenset("円元")

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
_MENTION_RE = re.compile(r"[@＠][^\s@]+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")

# 文字体系。かなと漢字は同じ日本語としてまとめる。
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9d]")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_LAO_RE = re.compile(r"[\u0e80-\u0eff]")
_KHMER_RE = re.compile(r"[\u1780-\u17ff]")
_MYANMAR_RE = re.compile(r"[\u1000-\u109f]")
_HEBREW_RE = re.compile(r"[\u0590-\u05ff]")
_ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_GREEK_RE = re.compile(r"[\u0370-\u03ff]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")

# 他の文字体系と同時に出たとき、混在とみなす最小文字数
_SCRIPT_MIN_CHARS = {
    "hangul": 1,
    "thai": 2,
    "lao": 2,
    "khmer": 2,
    "myanmar": 2,
    "hebrew": 2,
    "arabic": 2,
    "cyrillic": 2,
    "greek": 3,
    "devanagari": 2,
}


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _letters(text: str) -> list[str]:
    return [ch for ch in text if unicodedata.category(ch).startswith("L")]


class LanguageDetectionService:
    """メッセージ言語を判定するための薄いユーティリティ。"""

    def detect(self, text: str) -> str:
        if not text:
            return ""
        try:
            return detect(text)
        except LangDetectException:
            return ""

    def has_translatable_text(self, text: str) -> bool:
        """絵文字・数字・記号だけのメッセージは翻訳対象にしない。"""

        letters = _letters(_normalize(text))
        meaningful = [ch for ch in letters if ch not in _CURRENCY_LETTERS]
        return bool(meaningful)

    def is_mixed_language(self, text: str) -> bool:
        """全文が単一言語で書かれていないとき True。

        かなの中の LINE や Amazon のような固有名詞・頭字語は混在に数えない。
        meeting のような小文字の語や、別の文字体系（タイ文字など）は数える。
        """

        normalized = _normalize(text)
        if not self.has_translatable_text(normalized):
            return False

        groups: set[str] = set()
        if _KANA_RE.search(normalized):
            groups.add("ja")
        elif _HAN_RE.search(normalized):
            groups.add("cjk")

        for name, pattern in (
            ("hangul", _HANGUL_RE),
            ("thai", _THAI_RE),
            ("lao", _LAO_RE),
            ("khmer", _KHMER_RE),
            ("myanmar", _MYANMAR_RE),
            ("hebrew", _HEBREW_RE),
            ("arabic", _ARABIC_RE),
            ("cyrillic", _CYRILLIC_RE),
            ("greek", _GREEK_RE),
            ("devanagari", _DEVANAGARI_RE),
        ):
            found = pattern.findall(normalized)
            if len(found) >= _SCRIPT_MIN_CHARS[name]:
                groups.add(name)

        if _has_substantial_latin(_latin_scan_text(normalized)):
            groups.add("latin")

        return len(groups) >= 2


def _latin_scan_text(text: str) -> str:
    """URL・メール・メンションはラテン語の混在判定から外す。"""

    without_urls = _URL_RE.sub(" ", text)
    without_emails = _EMAIL_RE.sub(" ", without_urls)
    return _MENTION_RE.sub(" ", without_emails)


def _has_substantial_latin(text: str) -> bool:
    """文として読めるラテン文字があるか。単体の固有名詞と頭字語は除く。"""

    linguistic: list[str] = []
    for match in _LATIN_WORD_RE.findall(text):
        letters = re.sub(r"[^A-Za-z]", "", match)
        if len(letters) < 4 or letters.isupper():
            continue
        linguistic.append(letters)

    if any(word.islower() for word in linguistic):
        return True
    # Please Confirm Tonight のように、大文字始まりが複数ある文
    return len(linguistic) >= 2
