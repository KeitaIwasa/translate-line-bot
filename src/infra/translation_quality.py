"""翻訳1回ごとの品質シグナル。本文・訳文は出さない。

壊れた訳（漢字化け、同じ文字や語の反復、想定外の文字体系、cleanup で空になる）を
CloudWatch Logs Insights で数えるための数値とコードだけを、標準出力へ1行出す。
メトリクスは既存の KOTORI/Translation 名前空間の EMF。検知のみで、翻訳結果は変えない。

しきい値の根拠:
- 同一文字の連続は 5 以上を壊れた反復とみる（「ててててて」）。
  句読点・数字は 8 以上（「!!!!」「555」を拾わない）。
- 原文がその文字を2回以上重ねている、または原文自体が16文字以内の反復
  （www / 555555 / ㅋㅋㅋㅋ / 笑笑笑 / ！！！）なら、同じ種類の引き伸ばしは
  文字12・n-gram は繰り返し8かつ24文字以上まで上げる。
- 空白区切りの同一語は10回以上。空白の無い部分文字列は4回以上かつ12文字以上
  （「テスト」を4回）。原文に同じ並びが2回以上あれば対象外。
- 長さの比は、原文12文字以上で8倍以上、原文40文字以上で0.15倍未満。
  漢字かなの自然な訳は英語の2割前後まで短くなる（広東語で約0.20）ので、0.15まで許す。
  絵文字・数字・記号・URL だけの原文、短い反復の原文は比を見ない。
- ja の稀な漢字は、cp932 に無く、GB2312 にあり、Big5 に無い字（冈・东・门）。
  cp932 に無いだけの JIS X 0213 の字（嚙・𠮷）は日本語として残す。
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from typing import Iterable, Sequence

from src.infra.provider_failure import METRIC_NAMESPACE, _metric_stage
from src.presentation.list_layout import normalize_translated_user_text
from src.presentation.reply_formatter import strip_source_echo, strip_spurious_boundary_cjk

SIGNAL_VALUE = "translation_quality"
METRIC_CALLS = "TranslationQuality"
METRIC_SUSPECT = "TranslationSuspect"

# 同一文字。平仮名「て」が5連続する退化を拾い、笑いの3〜4連続は拾わない
LETTER_RUN_MIN = 5
MARK_RUN_MIN = 8
# 原文が短い反復のとき。笑い文字への引き伸ばし（笑を6個など）を外す
LETTER_RUN_MIN_SHORT_SOURCE = 12
MARK_RUN_MIN_SHORT_SOURCE = 16
SHORT_REPETITIVE_MAX_CHARS = 16
SHORT_SOURCE_MIN_RUN = 3
# 原文側に同じ文字がこの回数以上連続していれば、訳のその文字は反復扱いにしない
SOURCE_CHAR_REPEAT_MIN = 2

# 空白の無い n-gram。「テスト」4回 = 12文字
NGRAM_MIN_REPEATS = 4
NGRAM_MIN_COVERAGE = 12
NGRAM_MAX_UNIT = 8
NGRAM_MIN_REPEATS_SHORT_SOURCE = 8
NGRAM_MIN_COVERAGE_SHORT_SOURCE = 24
# 空白区切りの同一語。10回以上の退化
WORD_REPEAT_MIN = 10
# 原文に同じ語・n-gram がこの回数以上連続していれば、訳側だけでは壊れていない
SOURCE_NGRAM_REPEAT_MIN = 2

# 原文に対する出力長。短文・非言語の原文は比が暴れるので見ない
RATIO_HIGH = 8.0
RATIO_HIGH_MIN_SOURCE = 12
RATIO_LOW = 0.15
RATIO_LOW_MIN_SOURCE = 40

# 長い原文の n-gram 走査は先頭と末尾だけにする（全体の文字ランは全長）
NGRAM_SCAN_CHARS = 1500
LONG_SOURCE_CHARS = 400
SHORT_SOURCE_CHARS = 16

UNEXPECTED_SCRIPT_MIN = 1

REASON_REPEAT_RUN = "repeat_run"
REASON_NGRAM_REPEAT = "ngram_repeat"
REASON_RATIO_HIGH = "ratio_high"
REASON_RATIO_LOW = "ratio_low"
REASON_UNEXPECTED_SCRIPT = "unexpected_script"
REASON_RARE_HAN = "rare_han"
REASON_EMPTIED = "emptied_by_cleanup"
REASON_FINISH = "finish_not_stop"

REASONS = (
    REASON_REPEAT_RUN,
    REASON_NGRAM_REPEAT,
    REASON_RATIO_HIGH,
    REASON_RATIO_LOW,
    REASON_UNEXPECTED_SCRIPT,
    REASON_RARE_HAN,
    REASON_EMPTIED,
    REASON_FINISH,
)

# 比を見ない原文。URL や絵文字のエコーは壊れた訳ではない
_RATIO_SKIP_KINDS = frozenset(
    {"empty", "whitespace", "emoji_only", "numeric_only", "symbol_only", "url_only"}
)
# cleanup で空になっても suspect にしない原文。翻訳対象外のエコー
_EMPTY_SKIP_KINDS = _RATIO_SKIP_KINDS

_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_FINISH_RE = re.compile(r"^[A-Z0-9_]{1,32}$")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s]+", re.IGNORECASE)
_LIST_LINE_RE = re.compile(r"^(?:[0-9]{1,2}[.)．。]|[-•・*])\s*\S")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{4,}")

# ターゲット言語で自然な文字体系。未知の言語は unexpected_script を出さない
_ALLOWED_SCRIPTS: dict[str, frozenset[str]] = {
    "ja": frozenset({"hiragana", "katakana", "han", "latin"}),
    "ko": frozenset({"hangul", "han", "latin"}),
    "th": frozenset({"thai", "latin"}),
    "zh": frozenset({"han", "latin"}),
    "zh-cn": frozenset({"han", "latin"}),
    "zh-tw": frozenset({"han", "latin"}),
    "yue": frozenset({"han", "latin"}),
    "vi": frozenset({"latin"}),
    "ne": frozenset({"devanagari", "latin"}),
    "hi": frozenset({"devanagari", "latin"}),
    "my": frozenset({"myanmar", "latin"}),
    "id": frozenset({"latin"}),
    "es": frozenset({"latin"}),
    "fr": frozenset({"latin"}),
    "pt": frozenset({"latin"}),
    "de": frozenset({"latin"}),
    "en": frozenset({"latin"}),
    "ar": frozenset({"arabic", "latin"}),
    "fa": frozenset({"arabic", "latin"}),
    "ru": frozenset({"cyrillic", "latin"}),
    "uk": frozenset({"cyrillic", "latin"}),
}

_DOMINANT_ORDER = (
    "ja",
    "han",
    "hangul",
    "thai",
    "myanmar",
    "devanagari",
    "arabic",
    "cyrillic",
    "hebrew",
    "greek",
    "latin",
)

_NAME_TO_SCRIPT = (
    ("HALFWIDTH KATAKANA", "katakana"),
    ("HALFWIDTH HANGUL", "hangul"),
    ("HIRAGANA", "hiragana"),
    ("KATAKANA", "katakana"),
    ("HANGUL", "hangul"),
    ("CJK UNIFIED IDEOGRAPH", "han"),
    ("CJK COMPATIBILITY IDEOGRAPH", "han"),
    ("THAI", "thai"),
    ("LATIN", "latin"),
    ("ARABIC", "arabic"),
    ("CYRILLIC", "cyrillic"),
    ("DEVANAGARI", "devanagari"),
    ("MYANMAR", "myanmar"),
    ("HEBREW", "hebrew"),
    ("GREEK", "greek"),
    ("BENGALI", "bengali"),
    ("GURMUKHI", "gurmukhi"),
    ("GUJARATI", "gujarati"),
    ("TAMIL", "tamil"),
    ("TELUGU", "telugu"),
    ("KANNADA", "kannada"),
    ("MALAYALAM", "malayalam"),
    ("SINHALA", "sinhala"),
    ("LAO", "lao"),
    ("KHMER", "khmer"),
    ("GEORGIAN", "georgian"),
    ("ARMENIAN", "armenian"),
    ("ETHIOPIC", "ethiopic"),
)

# 長音記号は仮名混入に数えない
_IGNORED_LETTER_NAMES = frozenset(
    {
        "KATAKANA-HIRAGANA PROLONGED SOUND MARK",
        "HALFWIDTH KATAKANA-HIRAGANA PROLONGED SOUND MARK",
    }
)


def assess_translation_quality(
    *,
    model: str,
    response_body: dict | None,
    source_text: str,
    raw_items: Sequence[object],
    target_languages: Sequence[str],
) -> dict:
    """1回の翻訳の品質シグナル。ログ用の dict で、本文は含めない。"""

    source = source_text if isinstance(source_text, str) else ""
    profile = _source_profile(source)
    candidate = _first_candidate(response_body)
    usage = _usage(response_body)
    finish = _finish_reason(candidate)
    # 言語コードとして不安全なものは und に潰す。並びはリクエスト順
    safe_targets = [_safe_lang(lang) for lang in target_languages]
    allowed = {lang.lower() for lang in safe_targets if lang != "und"}

    outputs: list[dict] = []
    reasons: set[str] = set()
    if finish and finish != "STOP":
        reasons.add(REASON_FINISH)

    for lang, raw in _iter_raw_items(raw_items, allowed):
        output, output_reasons = _assess_output(source, profile, lang, raw)
        outputs.append(output)
        reasons.update(output_reasons)

    ordered = [code for code in REASONS if code in reasons]
    suspect = bool(ordered)
    payload = _base_payload(
        model=model,
        finish=finish,
        usage=usage,
        targets=safe_targets,
        profile=profile,
        outputs=outputs,
        reasons=ordered,
        suspect=suspect,
    )
    return payload


def emit_translation_quality(
    *,
    model: str,
    response_body: dict | None,
    source_text: str,
    raw_items: Sequence[object],
    target_languages: Sequence[str],
) -> dict:
    """品質シグナルを EMF 1行で出す。呼び出し側が例外を握る。"""

    payload = assess_translation_quality(
        model=model,
        response_body=response_body,
        source_text=source_text,
        raw_items=raw_items,
        target_languages=target_languages,
    )
    stage = _metric_stage()
    payload["Stage"] = stage
    payload["Suspect"] = "true" if payload["suspect"] else "false"
    payload["Signal"] = SIGNAL_VALUE
    payload[METRIC_CALLS] = 1
    payload[METRIC_SUSPECT] = 1 if payload["suspect"] else 0
    payload["_aws"] = {
        "Timestamp": int(time.time() * 1000),
        "CloudWatchMetrics": [
            {
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [["Stage"], ["Stage", "Suspect"]],
                "Metrics": [
                    {"Name": METRIC_CALLS, "Unit": "Count"},
                    {"Name": METRIC_SUSPECT, "Unit": "Count"},
                ],
            }
        ],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return payload


def _assess_output(source: str, profile: dict, lang: str, raw: str) -> tuple[dict, set[str]]:
    analyzed = _strip_controls(raw)
    chars = _content_len(analyzed)
    ratio = _ratio(chars, profile["chars"])
    max_run, run_suspect = _suspect_run(profile, analyzed, source)
    token_reps, token_unit = _max_token_repeat(analyzed)
    ngram_reps, ngram_unit = _max_segment_ngram(analyzed)
    max_ngram = max(token_reps, ngram_reps)
    emptied = _emptied_by_cleanup(source, raw)
    reasons: set[str] = set()

    if run_suspect:
        reasons.add(REASON_REPEAT_RUN)
    if _ngram_is_suspect(profile, token_reps, token_unit, ngram_reps, ngram_unit, source):
        reasons.add(REASON_NGRAM_REPEAT)
    if _ratio_is_high(profile, ratio):
        reasons.add(REASON_RATIO_HIGH)
    if _ratio_is_low(profile, ratio, emptied):
        reasons.add(REASON_RATIO_LOW)
    if _has_unexpected_script(lang, analyzed, source):
        reasons.add(REASON_UNEXPECTED_SCRIPT)
    if lang.lower().split("-")[0] == "ja" and _has_rare_han(analyzed, source):
        reasons.add(REASON_RARE_HAN)
    # www → wwwwww は echo 除去で空になるが、原文の反復なので壊れていない
    if emptied and profile["kind"] not in _EMPTY_SKIP_KINDS and not profile["repetitive"]:
        reasons.add(REASON_EMPTIED)

    output = {
        "lang": lang,
        "chars": chars,
        "ratio": ratio,
        "maxRun": max_run,
        "maxNgram": max_ngram,
        "emptied": emptied,
        "reasons": [code for code in REASONS if code in reasons],
    }
    return output, reasons


def _base_payload(
    *,
    model: str,
    finish: str | None,
    usage: dict,
    targets: Sequence[str],
    profile: dict,
    outputs: Sequence[dict],
    reasons: Sequence[str],
    suspect: bool,
) -> dict:
    reason_set = set(reasons)
    max_chars = max((item["chars"] for item in outputs), default=0)
    max_run = max((item["maxRun"] for item in outputs), default=0)
    ratios = [item["ratio"] for item in outputs if isinstance(item["ratio"], (int, float))]
    max_ratio = round(max(ratios), 2) if ratios else None
    payload: dict = {
        "message": SIGNAL_VALUE,
        "model": _safe_model(model),
        "finishReason": finish,
        "promptTokenCount": _token(usage, "promptTokenCount"),
        "candidatesTokenCount": _token(usage, "candidatesTokenCount"),
        "thoughtsTokenCount": _token(usage, "thoughtsTokenCount"),
        "targetCount": len(targets),
        "targetLangs": ",".join(targets),
        "sourceChars": profile["chars"],
        "sourceKind": profile["kind"],
        "sourceFlags": ",".join(profile["flags"]),
        "sourceScript": profile["script"],
        "maxOutChars": max_chars,
        "maxRun": max_run,
        "maxRatio": max_ratio,
        "suspect": suspect,
        "suspectN": 1 if suspect else 0,
        "reasons": ",".join(reasons),
        "outputs": list(outputs),
    }
    for code in REASONS:
        payload[f"reason_{code}"] = 1 if code in reason_set else 0
    seen: set[str] = set()
    for item in outputs:
        key = _field_lang(item["lang"])
        if not key or key in seen:
            continue
        seen.add(key)
        text_suspect = bool(item["reasons"]) or REASON_FINISH in reason_set
        payload[f"calls_{key}"] = 1
        payload[f"suspect_{key}"] = 1 if text_suspect else 0
    return payload


def _source_profile(source: str) -> dict:
    raw = source or ""
    visible = _strip_controls(raw)
    chars = _content_len(visible)
    without_urls = _URL_RE.sub(" ", visible)
    flags: list[str] = []
    if _URL_RE.search(visible):
        flags.append("has_url")
    if any(unicodedata.category(ch) == "Cf" for ch in raw):
        flags.append("zero_width")
    families = _family_counts(without_urls)
    if _is_mixed(families, without_urls):
        flags.append("mixed")
    if _is_list(visible):
        flags.append("list")
    if chars >= LONG_SOURCE_CHARS:
        flags.append("long")
    if 0 < chars <= SHORT_SOURCE_CHARS:
        flags.append("short")
    max_run, _, _ = _max_run(visible)
    repetitive = _is_short_repetitive(visible, max_run)
    if repetitive or max_run >= SOURCE_CHAR_REPEAT_MIN:
        flags.append("source_repeat")
    kind = _primary_kind(raw, visible, without_urls, flags)
    return {
        "chars": chars,
        "kind": kind,
        "flags": flags,
        "script": _dominant_script(families),
        "repetitive": repetitive,
    }


def _primary_kind(raw: str, visible: str, without_urls: str, flags: Sequence[str]) -> str:
    if not raw:
        return "empty"
    if not visible.strip():
        return "whitespace"
    if _is_emoji_only(visible):
        return "emoji_only"
    if _is_numeric_only(without_urls):
        return "numeric_only"
    if _is_symbol_only(visible):
        return "symbol_only"
    if "has_url" in flags and _is_symbol_or_empty(without_urls):
        return "url_only"
    if "mixed" in flags:
        return "mixed"
    if "list" in flags:
        return "list"
    if "long" in flags:
        return "long"
    if "short" in flags:
        return "short"
    return "text"


def _suspect_run(profile: dict, text: str, source: str) -> tuple[int, bool]:
    """文字と記号は別のしきい値で、各ランを見る。最長が記号でも短い仮名ランを隠さない。"""

    longest = 0
    suspect = False
    for char, length, is_letter in _iter_runs(text):
        if length > longest:
            longest = length
        if not suspect and _run_is_suspect(profile, length, char, is_letter, source):
            suspect = True
    return longest, suspect


def _run_is_suspect(profile: dict, length: int, char: str, is_letter: bool, source: str) -> bool:
    if length <= 1 or not char:
        return False
    if _max_char_run(source, char) >= SOURCE_CHAR_REPEAT_MIN:
        return False
    if is_letter:
        limit = LETTER_RUN_MIN_SHORT_SOURCE if profile["repetitive"] else LETTER_RUN_MIN
    else:
        limit = MARK_RUN_MIN_SHORT_SOURCE if profile["repetitive"] else MARK_RUN_MIN
    return length >= limit


def _ngram_is_suspect(
    profile: dict,
    token_reps: int,
    token_unit: str,
    ngram_reps: int,
    ngram_unit: str,
    source: str,
) -> bool:
    if token_reps >= WORD_REPEAT_MIN and token_unit and not _source_repeats_token(source, token_unit):
        return True
    repeats_needed = NGRAM_MIN_REPEATS_SHORT_SOURCE if profile["repetitive"] else NGRAM_MIN_REPEATS
    coverage_needed = NGRAM_MIN_COVERAGE_SHORT_SOURCE if profile["repetitive"] else NGRAM_MIN_COVERAGE
    if ngram_reps < repeats_needed or not ngram_unit:
        return False
    if ngram_reps * len(ngram_unit) < coverage_needed:
        return False
    return not _source_repeats_unit(source, ngram_unit)


def _ratio_is_high(profile: dict, ratio: float | None) -> bool:
    if ratio is None or profile["kind"] in _RATIO_SKIP_KINDS or profile["repetitive"]:
        return False
    if profile["chars"] < RATIO_HIGH_MIN_SOURCE:
        return False
    return ratio >= RATIO_HIGH


def _ratio_is_low(profile: dict, ratio: float | None, emptied: bool) -> bool:
    if emptied or ratio is None or profile["kind"] in _RATIO_SKIP_KINDS or profile["repetitive"]:
        return False
    if profile["chars"] < RATIO_LOW_MIN_SOURCE:
        return False
    return ratio <= RATIO_LOW


def _emptied_by_cleanup(source: str, raw: str) -> bool:
    """reply_formatter と同じ順で空になるか。返す訳文自体は変えない。"""

    if not raw or not raw.strip():
        return False
    stepped = strip_spurious_boundary_cjk(source, raw)
    stepped = strip_source_echo(source, stepped)
    stepped = strip_spurious_boundary_cjk(source, stepped)
    stepped = normalize_translated_user_text(source, stepped)
    return not (stepped or "").strip()


def _has_unexpected_script(lang: str, text: str, source: str) -> bool:
    allowed = _allowed_scripts(lang)
    if allowed is None:
        return False
    source_scripts = _scripts_in(source)
    counts: dict[str, int] = {}
    for char in _strip_controls(text):
        script = _script_of(char)
        if script is None or script in allowed or script in source_scripts:
            continue
        counts[script] = counts.get(script, 0) + 1
        if counts[script] >= UNEXPECTED_SCRIPT_MIN:
            return True
    return False


def _has_rare_han(text: str, source: str) -> bool:
    """ja では使わない簡体字。cp932 に無く GB2312 にだけある漢字。"""

    source_chars = set(source or "")
    for char in text:
        if char in source_chars or _script_of(char) != "han":
            continue
        if _is_simplified_only(char):
            return True
    return False


def _is_simplified_only(char: str) -> bool:
    # cp932 は日本語の JIS X 0208。GB2312 にあって Big5 に無い字は簡体字側
    if _can_encode(char, "cp932"):
        return False
    return _can_encode(char, "gb2312") and not _can_encode(char, "big5")


def _can_encode(char: str, encoding: str) -> bool:
    try:
        char.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _allowed_scripts(lang: str) -> frozenset[str] | None:
    key = (lang or "").strip().lower().replace("_", "-")
    if key in _ALLOWED_SCRIPTS:
        return _ALLOWED_SCRIPTS[key]
    base = key.split("-", 1)[0]
    return _ALLOWED_SCRIPTS.get(base)


def _scripts_in(text: str) -> set[str]:
    found: set[str] = set()
    for char in text or "":
        script = _script_of(char)
        if script:
            found.add(script)
    return found


def _script_of(char: str) -> str | None:
    if unicodedata.category(char)[:1] != "L":
        return None
    name = unicodedata.name(char, "")
    if not name or name in _IGNORED_LETTER_NAMES:
        return None
    for prefix, script in _NAME_TO_SCRIPT:
        if name.startswith(prefix):
            return script
    return None


def _family_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    han = 0
    kana = 0
    for char in text:
        script = _script_of(char)
        if script in {"hiragana", "katakana"}:
            kana += 1
        elif script == "han":
            han += 1
        elif script:
            counts[script] = counts.get(script, 0) + 1
    if kana:
        counts["ja"] = counts.get("ja", 0) + kana + han
    elif han:
        counts["han"] = han
    return counts


def _is_mixed(families: dict[str, int], text: str) -> bool:
    present = []
    for name, count in families.items():
        if name == "latin":
            if _has_substantial_latin(text):
                present.append(name)
            continue
        if count >= 2:
            present.append(name)
    return len(present) >= 2


def _has_substantial_latin(text: str) -> bool:
    words = _LATIN_WORD_RE.findall(text)
    if any(not word.isupper() for word in words):
        return True
    return len(words) >= 2


def _dominant_script(families: dict[str, int]) -> str:
    if not families:
        return "none"
    best = max(families.values())
    for name in _DOMINANT_ORDER:
        if families.get(name, 0) == best and best > 0:
            return name
    return max(families, key=families.get)


def _is_short_repetitive(text: str, max_run: int) -> bool:
    compact = "".join(ch for ch in text if not ch.isspace())
    if not compact or len(compact) > SHORT_REPETITIVE_MAX_CHARS:
        return False
    if len(set(compact)) == 1:
        return True
    return max_run >= SHORT_SOURCE_MIN_RUN


def _is_emoji_only(text: str) -> bool:
    has_emoji = False
    for char in text:
        if char.isspace():
            continue
        if _is_letter(char) or char.isdigit():
            return False
        if _is_emoji(char):
            has_emoji = True
    return has_emoji


def _is_numeric_only(text: str) -> bool:
    has_digit = False
    for char in text:
        if char.isspace() or char in ".,，．:：-+＋－/":
            continue
        if char.isdigit():
            has_digit = True
            continue
        return False
    return has_digit


def _is_symbol_only(text: str) -> bool:
    has_symbol = False
    for char in text:
        if char.isspace():
            continue
        if _is_letter(char) or char.isdigit() or _is_emoji(char):
            return False
        has_symbol = True
    return has_symbol


def _is_symbol_or_empty(text: str) -> bool:
    for char in text:
        if char.isspace():
            continue
        if _is_letter(char) or char.isdigit() or _is_emoji(char):
            return False
    return True


def _is_list(text: str) -> bool:
    if "\n" not in text:
        return False
    marks = 0
    for line in text.splitlines():
        if _LIST_LINE_RE.match(line.strip()):
            marks += 1
    return marks >= 2


def _is_letter(char: str) -> bool:
    return unicodedata.category(char)[:1] == "L"


def _is_emoji(char: str) -> bool:
    code = ord(char)
    if code >= 0x1F000:
        return True
    return unicodedata.category(char) == "So" and code >= 0x2600


def _iter_runs(text: str):
    current = ""
    length = 0
    for char in text:
        if char.isspace() or unicodedata.category(char) in {"Cf", "Cc"}:
            if length:
                yield current, length, _is_letter(current)
            current = ""
            length = 0
            continue
        if char == current:
            length += 1
            continue
        if length:
            yield current, length, _is_letter(current)
        current = char
        length = 1
    if length:
        yield current, length, _is_letter(current)


def _max_run(text: str) -> tuple[int, str, bool]:
    best = 0
    best_char = ""
    best_letter = False
    for char, length, is_letter in _iter_runs(text):
        if length > best:
            best = length
            best_char = char
            best_letter = is_letter
    return best, best_char, best_letter


def _max_char_run(text: str, char: str) -> int:
    best = 0
    length = 0
    for item in _strip_controls(text):
        if item == char:
            length += 1
            if length > best:
                best = length
        else:
            length = 0
    return best


def _max_token_repeat(text: str) -> tuple[int, str]:
    tokens = [token.casefold() for token in text.split() if token]
    best = 1 if tokens else 0
    unit = ""
    index = 0
    while index < len(tokens):
        end = index + 1
        while end < len(tokens) and tokens[end] == tokens[index]:
            end += 1
        reps = end - index
        if reps > best:
            best = reps
            unit = tokens[index]
        index = end
    return best, unit


def _max_segment_ngram(text: str) -> tuple[int, str]:
    best_reps = 0
    best_unit = ""
    best_cov = 0
    segments = text.split() or [text]
    for segment in segments:
        for window in _scan_windows(segment):
            reps, unit = _max_ngram_repeats(window)
            coverage = reps * len(unit)
            if coverage > best_cov:
                best_cov = coverage
                best_reps = reps
                best_unit = unit
    return best_reps, best_unit


def _scan_windows(text: str) -> Iterable[str]:
    if len(text) <= NGRAM_SCAN_CHARS * 2:
        yield text
        return
    yield text[:NGRAM_SCAN_CHARS]
    yield text[-NGRAM_SCAN_CHARS:]


def _max_ngram_repeats(segment: str) -> tuple[int, str]:
    length = len(segment)
    best_reps = 0
    best_unit = ""
    best_cov = 0
    if length < NGRAM_MIN_COVERAGE:
        return 0, ""
    max_unit = min(NGRAM_MAX_UNIT, length // 2)
    for unit in range(2, max_unit + 1):
        index = 0
        limit = length - unit * 2
        while index <= limit:
            piece = segment[index : index + unit]
            if len(set(piece)) < 2 or segment[index + unit : index + 2 * unit] != piece:
                index += 1
                continue
            reps = 2
            cursor = index + 2 * unit
            while cursor + unit <= length and segment[cursor : cursor + unit] == piece:
                reps += 1
                cursor += unit
            coverage = reps * unit
            if coverage > best_cov:
                best_cov = coverage
                best_reps = reps
                best_unit = piece
            index = cursor
    return best_reps, best_unit


def _source_repeats_token(source: str, token: str) -> bool:
    if not token:
        return False
    tokens = [item.casefold() for item in _strip_controls(source).split() if item]
    target = token.casefold()
    best = 0
    index = 0
    while index < len(tokens):
        if tokens[index] != target:
            index += 1
            continue
        end = index + 1
        while end < len(tokens) and tokens[end] == target:
            end += 1
        best = max(best, end - index)
        index = end
    return best >= SOURCE_NGRAM_REPEAT_MIN


def _source_repeats_unit(source: str, unit: str) -> bool:
    if len(unit) < 2:
        return False
    haystack = _strip_controls(source)
    needle = unit + unit
    return needle in haystack


def _ratio(out_len: int, source_len: int) -> float | None:
    if source_len <= 0:
        return None
    return round(out_len / source_len, 2)


def _content_len(text: str) -> int:
    return sum(1 for char in text if unicodedata.category(char) not in {"Cf", "Cc"})


def _strip_controls(text: str) -> str:
    """ゼロ幅や制御文字を除く。改行は箇条書きの判定に残す。"""

    if not text:
        return ""
    return "".join(
        char
        for char in text
        if char in "\n\r\t" or unicodedata.category(char) not in {"Cf", "Cc"}
    )


def _iter_raw_items(raw_items: Sequence[object], allowed: set[str]) -> Iterable[tuple[str, str]]:
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        lang = item.get("lang")
        text = item.get("text")
        if not isinstance(lang, str) or not isinstance(text, str) or not text.strip():
            continue
        safe = _safe_lang(lang)
        if allowed and safe.lower() not in allowed:
            continue
        yield safe, text


def _first_candidate(body: dict | None) -> dict:
    if not isinstance(body, dict):
        return {}
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return {}
    return candidates[0]


def _usage(body: dict | None) -> dict:
    if not isinstance(body, dict):
        return {}
    usage = body.get("usageMetadata")
    return usage if isinstance(usage, dict) else {}


def _finish_reason(candidate: dict) -> str | None:
    raw = candidate.get("finishReason") if isinstance(candidate, dict) else None
    if not isinstance(raw, str):
        return None
    token = raw.strip().upper()
    if not _FINISH_RE.match(token):
        return None
    return token


def _token(usage: dict, key: str) -> int | None:
    if key not in usage:
        return None
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_lang(lang: object) -> str:
    if not isinstance(lang, str):
        return "und"
    token = lang.strip()
    if _LANG_RE.match(token):
        return token
    return "und"


def _field_lang(lang: str) -> str:
    lowered = lang.strip().lower().replace("-", "_")
    if not lowered or lowered == "und":
        return ""
    if not re.match(r"^[a-z][a-z0-9_]{0,15}$", lowered):
        return ""
    return lowered


def _safe_model(model: object) -> str:
    if not isinstance(model, str):
        return ""
    cleaned = "".join(char for char in model if char.isalnum() or char in "._-")
    return cleaned[:80]
