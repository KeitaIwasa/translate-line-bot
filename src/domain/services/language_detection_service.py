from __future__ import annotations

import re
import unicodedata
from typing import Sequence

import langdetect.detector_factory as _langdetect_factory
from langdetect import LangDetectException, detect, DetectorFactory

# 乱数シードを固定して言語判定結果のブレを防ぐ
DetectorFactory.seed = 0

# 数値に付いた通貨文字だけを除く。円・元だけのメッセージは語として翻訳する。
_CURRENCY_AMOUNT_RE = re.compile(r"\d[\d,，.．]*\s*[円元]|[円元]\s*\d[\d,，.．]*")

# スキーム付き・www に加え、パスを含む素のドメインも除く。
# ru.wikipedia.org/wiki/Москва のように、パス側の非ラテン文字を文字体系へ数えない。
_URL_RE = re.compile(
    r"https?://\S+|www\.\S+"
    r"|(?<![\w@])(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s]*",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
_MENTION_RE = re.compile(r"[@＠][^\s@]+")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
# 文字（Unicode の letter）の連続。数字や記号は語に数えない。
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
# 分かち書きしない文字体系。1文字を1語として、同じ語数の下限を使う。
_NON_SPACING_WORD_RE = re.compile(
    r"[\u0e00-\u0e7f\u0e80-\u0eff\u1000-\u109f\u1780-\u17ff"
    r"\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9d"
    r"\u4e00-\u9fff\uac00-\ud7af]+"
)
# 文と節。小数（3.14）と桁区切り（3,000 や 12:30）では切らない。
# 数字の直後でも、次が数字でなければ文末（room 3. の次の文）として切る。
_CLAUSE_SPLIT_RE = re.compile(
    r"[\n\r]+"
    r"|[.!?。！？…؟।॥۔]+(?!\d)"
    r"|[;；]+"
    r"|(?<!\d)[,，、،](?!\d)"
    r"|(?<!\d)[:：](?!\d)"
    r"|(?:\s+[—–\-]\s+)"
)

# 同じ文字体系の文節を言語判定する下限。短い断片・頭字語・単語1つは混在にしない。
_MIN_SEGMENT_LETTERS = 20
_MIN_SEGMENT_WORDS = 4
_MIN_SEGMENT_PROB = 0.8
_MAX_SEGMENTS = 12

# langdetect のコードを文字体系へ対応付ける。
# グループ言語のうち同じ文字体系が2つ以上あるときは、文節ごとの判定に回す。
# 文字体系が違う組（かなとラテン文字など）は、文字体系の併存だけで混在になる。
_LANG_SCRIPTS: dict[str, str] = {}
for _script, _codes in (
    (
        "latin",
        (
            "af", "ca", "cs", "cy", "da", "de", "en", "es", "et", "fi", "fr", "hr", "hu",
            "id", "it", "lt", "lv", "nl", "no", "pl", "pt", "ro", "sk", "sl", "so", "sq",
            "sv", "sw", "tl", "tr", "vi",
        ),
    ),
    ("cyrillic", ("bg", "mk", "ru", "uk")),
    ("arabic", ("ar", "fa", "ur")),
    ("devanagari", ("hi", "mr", "ne")),
    ("bengali", ("bn",)),
    ("gurmukhi", ("pa",)),
    ("gujarati", ("gu",)),
    ("tamil", ("ta",)),
    ("telugu", ("te",)),
    ("kannada", ("kn",)),
    ("malayalam", ("ml",)),
    ("thai", ("th",)),
    ("hangul", ("ko",)),
    ("greek", ("el",)),
    ("hebrew", ("he",)),
    # かなと漢字は一つの文字体系。日本語と中国語の混在は文節で区別する。
    ("han", ("ja", "zh-cn", "zh-tw")),
):
    for _code in _codes:
        _LANG_SCRIPTS[_code] = _script

_LANGDETECT_ALIASES = {
    "zh": "zh-cn",
    "zh-hans": "zh-cn",
    "zh-hant": "zh-tw",
    "nb": "no",
    "nn": "no",
    "iw": "he",
    "in": "id",
}

# 文字体系。かなと漢字は同じ han としてまとめる。
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
_BENGALI_RE = re.compile(r"[\u0980-\u09ff]")
_GURMUKHI_RE = re.compile(r"[\u0a00-\u0a7f]")
_GUJARATI_RE = re.compile(r"[\u0a80-\u0aff]")
_TAMIL_RE = re.compile(r"[\u0b80-\u0bff]")
_TELUGU_RE = re.compile(r"[\u0c00-\u0c7f]")
_KANNADA_RE = re.compile(r"[\u0c80-\u0cff]")
_MALAYALAM_RE = re.compile(r"[\u0d00-\u0d7f]")
# かな・漢字とラテン文字以外。存在判定と文字ごとの文字体系で共用する。
_OTHER_SCRIPT_RES = (
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
    ("bengali", _BENGALI_RE),
    ("gurmukhi", _GURMUKHI_RE),
    ("gujarati", _GUJARATI_RE),
    ("tamil", _TAMIL_RE),
    ("telugu", _TELUGU_RE),
    ("kannada", _KANNADA_RE),
    ("malayalam", _MALAYALAM_RE),
)

# 別文字体系を混在とみなす下限。単語1つの人名（Мария など）は足りない。
# 2語以上かつ文字の約15%以上、またはその文字体系だけの節（2語以上）があるとき。
_MIN_SECONDARY_WORDS = 2
_MIN_SECONDARY_SHARE = 0.15
# 分かち書きしない文字体系。語数は1文字を1語として数える。
_NON_SPACING_SCRIPTS = frozenset({"han", "hangul", "thai", "lao", "khmer", "myanmar"})

# 他の文字体系と同時に出たとき、存在とみなす最小文字数。混在にするには上の割合も要る。
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
    "bengali": 2,
    "gurmukhi": 2,
    "gujarati": 2,
    "tamil": 2,
    "telugu": 2,
    "kannada": 2,
    "malayalam": 2,
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
        """絵文字・数字・記号だけのメッセージは翻訳対象にしない。

        円・元は 3,000円 のように数値へ付くときだけ除く。それ以外は語として残す。
        """

        normalized = _normalize(text)
        without_amounts = _CURRENCY_AMOUNT_RE.sub(" ", normalized)
        return bool(_letters(without_amounts))

    def is_mixed_language(self, text: str, group_languages: Sequence[str] | None = None) -> bool:
        """全文が単一言語で書かれていないとき True。

        かなの中の LINE や Amazon、英語文中の Мария のような単語1つは混在に数えない。
        別の文字体系は、2語以上かつ文字の約15%以上を占めるか、その文字体系だけの節が
        あるときだけ混在にする。日本語の文と英語の文が並ぶ場合は混在のまま。
        英語とスペイン語、アラビア語とペルシア語、日本語と中国語のように
        文字体系が同じ言語は、グループ言語に絞った文節ごとの判定で混在にする。
        """

        normalized = _normalize(text)
        if not self.has_translatable_text(normalized):
            return False
        scripts = _script_groups(normalized)
        if len(scripts) >= 2 and _cross_scripts_are_meaningful(normalized, scripts):
            return True
        if not group_languages:
            return False
        return _same_script_languages_mixed(normalized, group_languages)


def _script_groups(text: str) -> set[str]:
    """テキストに含まれる文字体系。かなと漢字は同時にあっても一つの文字体系にまとめる。"""

    text = _strip_nonlinguistic(text)
    groups: set[str] = set()
    if _KANA_RE.search(text) or _HAN_RE.search(text):
        groups.add("han")

    for name, pattern in _OTHER_SCRIPT_RES:
        found = pattern.findall(text)
        if len(found) >= _SCRIPT_MIN_CHARS[name]:
            groups.add(name)

    if _has_substantial_latin(text):
        groups.add("latin")
    return groups


def _cross_scripts_are_meaningful(text: str, scripts: set[str]) -> bool:
    """2つ以上の文字体系が、それぞれ本文として十分か、独立した節を占めるか。"""

    cleaned = _strip_nonlinguistic(text)
    letter_counts = _script_letter_counts(cleaned)
    total = sum(letter_counts.values())
    if total <= 0:
        return False
    word_counts = _script_word_counts(cleaned)
    clause_scripts = _exclusive_clause_scripts(cleaned)
    meaningful = 0
    for script in scripts:
        words = word_counts.get(script, 0)
        share = letter_counts.get(script, 0) / total
        if words >= _MIN_SECONDARY_WORDS and share >= _MIN_SECONDARY_SHARE:
            meaningful += 1
            continue
        if script in clause_scripts:
            meaningful += 1
    return meaningful >= 2


def _exclusive_clause_scripts(text: str) -> set[str]:
    """その文字体系の文字だけで、2語以上ある節。"""

    found: set[str] = set()
    for segment in _CLAUSE_SPLIT_RE.split(text):
        segment = segment.strip()
        if not segment:
            continue
        counts = _script_letter_counts(segment)
        if len(counts) != 1:
            continue
        script = next(iter(counts))
        if _script_word_counts(segment).get(script, 0) >= _MIN_SECONDARY_WORDS:
            found.add(script)
    return found


def _script_letter_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        script = _char_script(ch)
        if script:
            counts[script] = counts.get(script, 0) + 1
    return counts


def _script_word_counts(text: str) -> dict[str, int]:
    """文字体系ごとの語数。分かち書きしない文字は1文字を1語とする。"""

    counts: dict[str, int] = {}
    for token in _WORD_RE.findall(text):
        run_script = ""
        run_len = 0
        for ch in token:
            script = _char_script(ch)
            if script and script == run_script:
                run_len += 1
                continue
            _add_script_words(counts, run_script, run_len)
            run_script = script or ""
            run_len = 1 if script else 0
        _add_script_words(counts, run_script, run_len)
    return counts


def _add_script_words(counts: dict[str, int], script: str, length: int) -> None:
    if not script or length <= 0:
        return
    added = length if script in _NON_SPACING_SCRIPTS else 1
    counts[script] = counts.get(script, 0) + added


def _char_script(ch: str) -> str | None:
    if _KANA_RE.match(ch) or _HAN_RE.match(ch):
        return "han"
    for name, pattern in _OTHER_SCRIPT_RES:
        if pattern.match(ch):
            return name
    if "A" <= ch <= "Z" or "a" <= ch <= "z":
        return "latin"
    return None


def _strip_nonlinguistic(text: str) -> str:
    """URL・メール・メンションは、どの文字体系の判定にも使わない。"""

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


def _same_script_languages_mixed(text: str, group_languages: Sequence[str]) -> bool:
    """同じ文字体系のグループ言語が、それぞれ十分な文節を占めるとき True。"""

    scripts = _script_groups(text)
    candidates = _group_languages_for_scripts(group_languages, scripts)
    if len(candidates) < 2:
        return False

    cleaned = _strip_nonlinguistic(text)
    letters, _words = _linguistic_stats(cleaned)
    # 言語が2つとも下限を満たすには、全体でその2倍の文字が要る
    if letters < _MIN_SEGMENT_LETTERS * 2:
        return False

    covered: dict[str, int] = {}
    checked = 0
    for segment in _CLAUSE_SPLIT_RE.split(cleaned):
        segment = segment.strip()
        if not segment:
            continue
        segment_letters, segment_words = _linguistic_stats(segment)
        if segment_letters < _MIN_SEGMENT_LETTERS or segment_words < _MIN_SEGMENT_WORDS:
            continue
        detected = _detect_restricted(segment, candidates)
        checked += 1
        if detected is None:
            if checked >= _MAX_SEGMENTS:
                break
            continue
        lang, prob = detected
        if prob >= _MIN_SEGMENT_PROB and lang in candidates:
            covered[lang] = covered.get(lang, 0) + segment_letters
        if len([code for code, count in covered.items() if count >= _MIN_SEGMENT_LETTERS]) >= 2:
            return True
        if checked >= _MAX_SEGMENTS:
            break
    return False


def _group_languages_for_scripts(group_languages: Sequence[str], scripts: set[str]) -> list[str]:
    """テキストの文字体系と一致するグループ言語だけを、langdetect のコードで返す。"""

    codes: list[str] = []
    for raw in group_languages:
        code = _to_langdetect_code(raw)
        if not code or _LANG_SCRIPTS.get(code) not in scripts:
            continue
        if code not in codes:
            codes.append(code)
    return codes


def _to_langdetect_code(raw: str) -> str:
    code = (raw or "").strip().lower().replace("_", "-")
    if not code:
        return ""
    if code in _LANGDETECT_ALIASES:
        return _LANGDETECT_ALIASES[code]
    primary = code.split("-", 1)[0]
    return _LANGDETECT_ALIASES.get(primary, primary)


def _strip_glued_latin_acronym(word: str) -> str:
    """全大文字のラテン文字だけを除く。LINEで明日… の日本語は残す。"""

    latin = [ch for ch in word if "A" <= ch <= "Z" or "a" <= ch <= "z"]
    if latin and all("A" <= ch <= "Z" for ch in latin):
        return re.sub(r"[A-Za-z]", "", word)
    return word


def _linguistic_stats(text: str) -> tuple[int, int]:
    """頭字語を除いた文字数と語数。LINE のような全大文字のラテン文字は数えない。

    LINEで明日… のように頭字語が日本語へ接しているときは、ラテン部分だけを除き、
    同じトークンの非ラテン文字は残す。
    漢字・かな・ハングル・タイ文字など分かち書きしない文字は、1文字を1語として数える。
    文字数と語数の下限はラテン文字と同じまま、文節の長さだけを比べられるようにする。
    """

    letters = 0
    words = 0
    for raw in _WORD_RE.findall(text):
        word = _strip_glued_latin_acronym(raw)
        if len(word) < 2 or word.isupper():
            continue
        letters += len(word)
        if _NON_SPACING_WORD_RE.fullmatch(word):
            words += len(word)
        else:
            words += 1
    return letters, words


def _detect_restricted(text: str, languages: Sequence[str]) -> tuple[str, float] | None:
    """グループ言語だけを事前確率にして、最有力の言語と確度を返す。"""

    _langdetect_factory.init_factory()
    known = set(_langdetect_factory._factory.get_lang_list())
    prior = {lang: 1.0 for lang in languages if lang in known}
    if len(prior) < 2:
        return None
    detector = _langdetect_factory._factory.create()
    detector.set_prior_map(prior)
    detector.append(text)
    try:
        probabilities = detector.get_probabilities()
    except LangDetectException:
        return None
    if not probabilities:
        return None
    best = probabilities[0]
    return best.lang, float(best.prob)
