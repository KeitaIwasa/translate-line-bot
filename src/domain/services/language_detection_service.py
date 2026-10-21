from __future__ import annotations

import unicodedata
from typing import Mapping, Sequence

from langdetect import DetectorFactory, LangDetectException, detect
from langdetect.detector_factory import DetectorFactory as _DetectorFactory

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
# langdetect のプロファイル名。zh は簡体として zh-cn を使う
_PROFILE_OF = {"zh": "zh-cn", "zh-cn": "zh-cn", "zh-tw": "zh-tw"}
_RESTRICTED_FACTORIES: dict[frozenset[str], _DetectorFactory] = {}
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
        # ルーターが付けた言語は、本文の文字種に合い、同じ文字体系のグループ言語と矛盾しないときだけ使う
        if (
            preferred_code
            and _language_fits(preferred_code, signals)
            and _preferred_agrees_with_group(preferred_code, group)
        ):
            return preferred_code

        # かなは日本語、ハングルは韓国語。他の言語へ落とさない
        if signals["kana"] > 0 and signals["kana"] >= signals["hangul"]:
            return "ja"
        if signals["hangul"] > 0:
            return "ko"

        script = _primary_script(signals)
        if not script:
            # 未分類の文字があるときは group[0] に落とさず、生の langdetect を使う
            if signals["other"] > 0:
                raw = _normalize_code(self.detect(text) if detected is None else detected)
                if raw:
                    return raw
            return group[0] if group else ""
        return _resolve_script(script, text or "", signals, group, detected)


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
    """そのコードが本文の文字種で説明できるときだけ True。漢字だけでは韓国語にしない。"""

    kana = signals["kana"]
    hangul = signals["hangul"]
    han = signals["han"]
    if code == "ko":
        return hangul > 0 and hangul >= kana
    script = _script_of(code)
    if script == "han" and code == "ja":
        if hangul > kana:
            return False
        return kana > 0 or (han > 0 and hangul == 0)
    if script == "han":
        return han > 0 and kana == 0 and hangul == 0
    if script == "latin":
        return signals["latin"] > 0 and kana == 0 and hangul == 0 and han == 0 and not _other_script(signals)
    if script in _SCRIPT_LANGUAGES and script != "han":
        return signals.get(script, 0) > 0
    return False


def _other_script(signals: Mapping[str, int]) -> bool:
    return any(signals[name] > 0 for name in _SCRIPT_LANGUAGES if name != "han")


def _script_of(code: str) -> str:
    if code in _LATIN_LANGUAGES:
        return "latin"
    for script, languages in _SCRIPT_LANGUAGES.items():
        if code in languages:
            return script
    if code.startswith("zh"):
        return "han"
    return ""


def _primary_script(signals: Mapping[str, int]) -> str:
    """かな・ハングル以外で、本文を代表する文字体系。漢字はラテン文字より先に見る。"""

    if signals["han"] > 0:
        return "han"
    best = ""
    best_count = 0
    for name in _SCRIPT_LANGUAGES:
        if name == "han":
            continue
        if signals[name] > best_count:
            best = name
            best_count = signals[name]
    if best:
        return best
    if signals["latin"] > 0:
        return "latin"
    return ""


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
        detector = _factory_for(profiles).create()
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


def _factory_for(profiles: frozenset[str]) -> _DetectorFactory:
    cached = _RESTRICTED_FACTORIES.get(profiles)
    if cached is not None:
        return cached
    import langdetect.detector_factory as detector_factory

    detector_factory.init_factory()
    base = detector_factory._factory
    indices = [index for index, lang in enumerate(base.langlist) if lang in profiles]
    factory = _DetectorFactory()
    factory.seed = 0
    factory.langlist = [base.langlist[index] for index in indices]
    for word, probs in base.word_lang_prob_map.items():
        sliced = [probs[index] for index in indices]
        if any(sliced):
            factory.word_lang_prob_map[word] = sliced
    _RESTRICTED_FACTORIES[profiles] = factory
    return factory
