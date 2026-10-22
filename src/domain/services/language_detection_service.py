from __future__ import annotations

import re
import unicodedata
from typing import Mapping, Sequence

import langdetect.detector_factory as _langdetect_factory
from langdetect import DetectorFactory, LangDetectException, detect
from langdetect.detector import Detector

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

# 同じ文字体系を複数言語が共有する。かなは日本語、ハングルは韓国語なのでここには入れない
_SCRIPT_LANGUAGES: dict[str, frozenset[str]] = {
    "arabic": frozenset({"ar", "fa", "ur", "ps"}),
    "cyrillic": frozenset({"ru", "uk", "bg", "sr", "mk", "be"}),
    "devanagari": frozenset({"hi", "mr", "ne"}),
    # 漢字だけだと日本語・簡体中国語・繁体中国語が区別できない
    "han": frozenset({"ja", "zh", "zh-cn", "zh-tw"}),
    "thai": frozenset({"th"}),
    "hebrew": frozenset({"he"}),
    "greek": frozenset({"el"}),
}
# グループにも判定結果にも無いときの既定
_SCRIPT_DEFAULT = {
    "arabic": "ar",
    "cyrillic": "ru",
    "devanagari": "hi",
    "han": "ja",
    "latin": "en",
    "thai": "th",
    "hebrew": "he",
    "greek": "el",
}
# 一言語にほぼ対応する文字体系。共有文字体系と同じく、グループ言語 → 制限した判定 → 既定
_SCRIPT_LANGUAGES.update(
    {
        "bengali": frozenset({"bn"}),
        "gurmukhi": frozenset({"pa"}),
        "gujarati": frozenset({"gu"}),
        "tamil": frozenset({"ta"}),
        "telugu": frozenset({"te"}),
        "kannada": frozenset({"kn"}),
        "malayalam": frozenset({"ml"}),
        "sinhala": frozenset({"si"}),
        "lao": frozenset({"lo"}),
        "myanmar": frozenset({"my"}),
        "georgian": frozenset({"ka"}),
        "armenian": frozenset({"hy"}),
        "ethiopic": frozenset({"am"}),
        "khmer": frozenset({"km"}),
    }
)
_SCRIPT_DEFAULT.update(
    {
        "bengali": "bn",
        "gurmukhi": "pa",
        "gujarati": "gu",
        "tamil": "ta",
        "telugu": "te",
        "kannada": "kn",
        "malayalam": "ml",
        "sinhala": "si",
        "lao": "lo",
        "myanmar": "my",
        "georgian": "ka",
        "armenian": "hy",
        "ethiopic": "am",
        "khmer": "km",
    }
)
# (開始, 終了, カウンター名)。かな・ハングル・漢字・ラテン文字は別扱い
_SCRIPT_RANGES = (
    (0x0370, 0x03FF, "greek"),
    (0x0400, 0x04FF, "cyrillic"),
    (0x0530, 0x058F, "armenian"),
    (0x0590, 0x05FF, "hebrew"),
    (0x0600, 0x06FF, "arabic"),
    (0x0750, 0x077F, "arabic"),
    (0x0900, 0x097F, "devanagari"),
    (0x0980, 0x09FF, "bengali"),
    (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0A80, 0x0AFF, "gujarati"),
    (0x0B80, 0x0BFF, "tamil"),
    (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"),
    (0x0D00, 0x0D7F, "malayalam"),
    (0x0D80, 0x0DFF, "sinhala"),
    (0x0E00, 0x0E7F, "thai"),
    (0x0E80, 0x0EFF, "lao"),
    (0x1000, 0x109F, "myanmar"),
    (0x10A0, 0x10FF, "georgian"),
    (0x1200, 0x137F, "ethiopic"),
    (0x1780, 0x17FF, "khmer"),
)
# URL とメンションは文字数にも言語判定にも使わない。日本語の本文まで巻き込まない
_REPLY_URL_RE = re.compile(
    r"(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    re.IGNORECASE,
)
_REPLY_MENTION_RE = re.compile(r"[@＠][^\s@＠]+")
# 同数のときはラテン文字より、かな・ハングル・漢字や他の文字体系を優先する
_FAMILY_TIE_BREAK = (
    "kana",
    "hangul",
    "han",
    "arabic",
    "cyrillic",
    "devanagari",
    "bengali",
    "gurmukhi",
    "gujarati",
    "tamil",
    "telugu",
    "kannada",
    "malayalam",
    "sinhala",
    "thai",
    "lao",
    "myanmar",
    "khmer",
    "hebrew",
    "greek",
    "georgian",
    "armenian",
    "ethiopic",
    "latin",
    "other",
)
_FAMILY_TIE_RANK = {name: index for index, name in enumerate(_FAMILY_TIE_BREAK)}

# langdetect のプロファイル名。zh は簡体として zh-cn を使う
_PROFILE_OF = {"zh": "zh-cn", "zh-cn": "zh-cn", "zh-tw": "zh-tw"}
# 言語集合ごとの単語確率表は複製しない。キャッシュは持たない（上限 0）
_FACTORY_CACHE_LIMIT = 0
_RESTRICTED_FACTORIES: dict[frozenset[str], Detector] = {}
_PROFILE_NAMES: frozenset[str] | None = None

_EMPTY_SIGNALS = {
    name: 0
    for name in (
        "kana",
        "hangul",
        "han",
        "latin",
        "other",
        *_SCRIPT_LANGUAGES.keys(),
    )
}


class LanguageDetectionService:
    """メッセージ言語を判定する。

    detect() は langdetect の生の結果を返す。返信言語は resolve_instruction_language() を使い、
    文字数の多い文字体系を選んでからグループ言語と制限した langdetect で決める。
    """

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



    def resolve_instruction_language(
        self,
        text: str,
        *,
        preferred: str = "",
        group_languages: Sequence[str] = (),
        detected: str | None = None,
    ) -> str:
        """返信に使う言語コードを返す。判定できないときはグループ言語、それも無ければ空。"""

        analyzed = _strip_urls_and_mentions(text or "")
        signals = _script_signals(analyzed)
        group = _normalize_group(group_languages)
        preferred_code = _normalize_code(preferred)
        # ルーターが付けた言語は、主要文字体系に合い、同じ文字体系のグループ言語と矛盾しないときだけ使う
        if (
            preferred_code
            and _language_fits(preferred_code, signals)
            and _preferred_agrees_with_group(preferred_code, group)
        ):
            return preferred_code

        family = _dominant_family(signals)
        if family == "kana":
            # かながハングル以上の文は日本語。漢字だけの文を ko と誤検知してもここには来ない
            return "ja"
        if family == "hangul":
            return "ko"
        if family == "other":
            raw = _normalize_code(self.detect(analyzed) if detected is None else detected)
            if raw:
                return raw
        if not family or family == "other":
            return group[0] if group else ""
        return _resolve_script(family, analyzed, signals, group, detected)


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


def _strip_urls_and_mentions(text: str) -> str:
    """URL と @メンションを除く。返信言語の文字数に入れない。"""

    without_urls = _REPLY_URL_RE.sub(" ", text)
    return _REPLY_MENTION_RE.sub(" ", without_urls)


def _is_emoji(codepoint: int) -> bool:
    """絵文字と結合子。文字数の分母に入れない。"""

    if codepoint in {0x200D, 0xFE0E, 0xFE0F}:
        return True
    return (
        0x2300 <= codepoint <= 0x23FF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2B00 <= codepoint <= 0x2BFF
        or 0x1F000 <= codepoint <= 0x1FAFF
    )


def _script_signals(text: str) -> Mapping[str, int]:
    counts = dict(_EMPTY_SIGNALS)
    for char in text:
        if char == "\ufffd" or unicodedata.category(char).startswith("C"):
            continue
        codepoint = ord(char)
        if _is_emoji(codepoint):
            continue
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
        elif (script_name := _script_name_for(codepoint)) and unicodedata.category(char).startswith("L"):
            # 句点（ベンガル語の । など）は文字体系の根拠にしない
            counts[script_name] += 1
        elif unicodedata.category(char).startswith("L"):
            # まだ対応していない文字体系。返信言語は生の langdetect に任せる
            counts["other"] += 1
    return counts


def _script_name_for(codepoint: int) -> str:
    for start, end, name in _SCRIPT_RANGES:
        if start <= codepoint <= end:
            return name
    return ""


def _is_latin_letter(char: str, codepoint: int) -> bool:
    if ("A" <= char <= "Z") or ("a" <= char <= "z") or 0xFF21 <= codepoint <= 0xFF3A or 0xFF41 <= codepoint <= 0xFF5A:
        return True
    if 0x00C0 <= codepoint <= 0x024F and unicodedata.category(char).startswith("L"):
        return True
    return False


def _language_fits(code: str, signals: Mapping[str, int]) -> bool:
    """そのコードが主要文字体系で説明できるときだけ True。少数のかな・漢字では日本語にしない。"""

    family = _dominant_family(signals)
    if code == "ko":
        return family == "hangul"
    if code == "ja":
        # かながある文と、漢字だけの文。漢字だけは中国語にも合う
        return family in {"kana", "han"}
    script = _script_of(code)
    if script == "han":
        return family == "han"
    if not script:
        return False
    return family == script


def _script_of(code: str) -> str:
    if code in _LATIN_LANGUAGES:
        return "latin"
    for script, languages in _SCRIPT_LANGUAGES.items():
        if code in languages:
            return script
    if code.startswith("zh"):
        return "han"
    return ""


def _dominant_family(signals: Mapping[str, int]) -> str:
    """文字数が最大の文字体系。かながある文の漢字は日本語の文字数に含める。"""

    kana = signals["kana"]
    hangul = signals["hangul"]
    han = signals["han"]
    scores: list[tuple[int, int, str]] = []

    def add(name: str, count: int) -> None:
        if count <= 0:
            return
        # 同数なら定義順が先の文字体系を採る
        rank = _FAMILY_TIE_RANK.get(name, len(_FAMILY_TIE_RANK))
        scores.append((count, -rank, name))

    if kana > 0 and kana >= hangul:
        add("kana", kana + han)
    elif hangul > 0:
        add("hangul", hangul + han)
    else:
        add("han", han)
    add("latin", signals["latin"])
    for name in _SCRIPT_LANGUAGES:
        if name == "han":
            continue
        add(name, signals.get(name, 0))
    add("other", signals["other"])
    if not scores:
        return ""
    scores.sort()
    return scores[-1][2]


def _group_matches(group: Sequence[str], script: str) -> list[str]:
    return [code for code in group if _script_of(code) == script]


def _same_code(left: str, right: str) -> bool:
    if left == right:
        return True
    return left in {"zh", "zh-cn"} and right in {"zh", "zh-cn"}


def _code_in(code: str, pool: Sequence[str]) -> bool:
    return any(_same_code(code, item) for item in pool)


def _preferred_agrees_with_group(code: str, group: Sequence[str]) -> bool:
    """グループがその文字体系の言語を一つだけ持つとき、それが候補と一致する場合だけ先に採用する。

    複数あるときは本文側の判定で分ける。別の言語がグループにある候補はここでは採らない。
    """

    script = _script_of(code)
    if not script:
        return True
    matches = _group_matches(group, script)
    if not matches:
        return True
    if len(matches) != 1:
        return False
    return _code_in(code, matches)


def _language_options(script: str) -> tuple[str, ...]:
    if script == "latin":
        return tuple(sorted(_LATIN_LANGUAGES))
    languages = _SCRIPT_LANGUAGES[script]
    default = _SCRIPT_DEFAULT[script]
    rest = tuple(sorted(code for code in languages if code != default))
    return (default, *rest)


def _resolve_script(
    script: str,
    text: str,
    signals: Mapping[str, int],
    group: Sequence[str],
    detected: str | None,
) -> str:
    """共有文字体系は、グループ言語 → その集合に限った langdetect → 既定、の順で決める。"""

    matches = _group_matches(group, script)
    options = _language_options(script)
    if len(matches) == 1:
        # ラテン文字は言語差が読み取れるので、十分な長さで別言語と分かったときだけグループ外を使う
        if script == "latin" and signals["latin"] >= _MIN_LATIN_LETTERS:
            found = _detect_in_pool(text, options, detected)
            if found and not _same_code(found, matches[0]):
                return found
        return matches[0]

    pool: Sequence[str] = matches if len(matches) > 1 else options
    found = _detect_in_pool(text, pool, detected)
    if not found and len(matches) > 1:
        wider = _detect_in_pool(text, options, detected)
        if _code_in(wider, matches):
            found = wider
    if found:
        for code in matches:
            if _same_code(found, code):
                return code
        return found
    if matches:
        return matches[0]
    return _SCRIPT_DEFAULT[script]


def _detect_in_pool(text: str, pool: Sequence[str], detected: str | None) -> str:
    """候補集合に制限して判定する。制限できなかったときだけ、渡された判定を候補内なら使う。"""

    found = _restricted_detect(text, pool)
    if found:
        return found
    hinted = _normalize_code(detected or "")
    if hinted and _code_in(hinted, pool):
        return hinted
    return ""


def _restricted_detect(text: str, pool: Sequence[str]) -> str:
    profile_to_code: dict[str, str] = {}
    for code in pool:
        profile = _PROFILE_OF.get(code, code)
        if profile not in _profile_names():
            continue
        profile_to_code.setdefault(profile, code)
    profiles = frozenset(profile_to_code)
    if len(profiles) < 2:
        return ""
    try:
        detector = _detector_for(profiles)
        detector.append(text)
        profile = detector.detect()
    except LangDetectException:
        return ""
    return profile_to_code.get(profile, "")


def _profile_names() -> frozenset[str]:
    global _PROFILE_NAMES
    if _PROFILE_NAMES is None:
        import langdetect.detector_factory as detector_factory

        detector_factory.init_factory()
        _PROFILE_NAMES = frozenset(detector_factory._factory.langlist)
    return _PROFILE_NAMES


def _detector_for(profiles: frozenset[str]) -> Detector:
    """共有プロファイルの添字だけで判定する。単語確率表は複製しない。"""

    import langdetect.detector_factory as detector_factory

    detector_factory.init_factory()
    base = detector_factory._factory
    indices = [index for index, lang in enumerate(base.langlist) if lang in profiles]
    return _SharedProfileDetector(base, indices)


class _SharedProfileDetector(Detector):
    """共有の word_lang_prob_map を読み、候補言語の確率だけを更新する。"""

    def __init__(self, factory, indices: Sequence[int]):
        super().__init__(factory)
        self._indices = tuple(indices)
        self.langlist = [factory.langlist[index] for index in self._indices]

    def _init_probability(self):
        size = len(self._indices)
        return [1.0 / size] * size

    def _extract_ngrams(self):
        """候補言語の確率がすべて 0 の語は、複製した表から落ちるのと同じように捨てる。"""

        from langdetect.utils.ngram import NGram

        result = []
        ngram = NGram()
        span = range(1, NGram.N_GRAM + 1)
        indices = self._indices
        prob_map = self.word_lang_prob_map
        for char in self.text:
            ngram.add_char(char)
            if ngram.capitalword:
                continue
            for length in span:
                if len(ngram.grams) < length:
                    break
                word = ngram.grams[-length:]
                if not word or word == " ":
                    continue
                probs = prob_map.get(word)
                if probs is None:
                    continue
                if any(probs[index] for index in indices):
                    result.append(word)
        return result

    def _update_lang_prob(self, prob, word, alpha):
        if not word or word not in self.word_lang_prob_map:
            return False
        lang_prob_map = self.word_lang_prob_map[word]
        weight = alpha / self.BASE_FREQ
        for offset, index in enumerate(self._indices):
            prob[offset] *= weight + lang_prob_map[index]
        return True
