"""無料枠の上限通知を LINE Flex Message として組み立てる。

見出し・本文・ボタンは主要言語をコード内の固定訳にする。
実行時の LLM 翻訳は、固定訳が無い言語だけに使い、長さ・繰り返し・文字体系で落とす。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

# 英語をベース原文にする。固定訳が無い言語のフォールバックもこの英文。
FREE_LIMIT_HEADLINE = "Free limit reached"
FREE_LIMIT_BUTTON_LABEL = "View plans"

# LINE Flex の上限。ボタンラベルは 40、altText は 400、URI は 1000。
# 本文は API 上限より短く保ち、言語が増えてもバブルが縦に伸びすぎないようにする。
FLEX_ALT_TEXT_LIMIT = 400
FLEX_BUTTON_LABEL_LIMIT = 40
FLEX_URI_LIMIT = 1000
FLEX_HEADLINE_LIMIT = 80
FLEX_BODY_LINE_LIMIT = 90
FLEX_BODY_MAX_LINES = 2
MAX_FLEX_LANGUAGES = 5

_KANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_THAI = re.compile(r"[\u0e00-\u0e7f]")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_ARABIC = re.compile(r"[\u0600-\u06ff]")
_DEVANAGARI = re.compile(r"[\u0900-\u097f]")
_BENGALI = re.compile(r"[\u0980-\u09ff]")
_TAMIL = re.compile(r"[\u0b80-\u0bff]")
_GREEK = re.compile(r"[\u0370-\u03ff]")
_HEBREW = re.compile(r"[\u0590-\u05ff]")
_MYANMAR = re.compile(r"[\u1000-\u109f]")
_KHMER = re.compile(r"[\u1780-\u17ff]")
_LAO = re.compile(r"[\u0e80-\u0eff]")
_SINHALA = re.compile(r"[\u0d80-\u0dff]")


@dataclass(frozen=True)
class _NoticeCopy:
    """日付と件数だけ差し込む固定文。"""

    headline: str
    button: str
    used: str
    resumes_on: str
    resumes_later: str


def _copy(
    headline: str,
    button: str,
    used: str,
    resumes_on: str,
    resumes_later: str,
) -> _NoticeCopy:
    return _NoticeCopy(headline, button, used, resumes_on, resumes_later)


# 主要言語は実行時に翻訳しない。{limit} と {date} だけ差し込む。
_STATIC_COPY: dict[str, _NoticeCopy] = {
    "en": _copy(
        FREE_LIMIT_HEADLINE,
        FREE_LIMIT_BUTTON_LABEL,
        "This month's {limit} free messages are used up.",
        "Translation resumes on {date} UTC.",
        "Translation resumes when the monthly quota resets.",
    ),
    "ja": _copy(
        "無料枠の上限です",
        "プランを見る",
        "今月の無料メッセージ{limit}件を使い切りました。",
        "{date} UTC に翻訳が再開します。",
        "月間の上限がリセットされると翻訳が再開します。",
    ),
    "ko": _copy(
        "무료 한도에 도달했습니다",
        "요금제 보기",
        "이번 달 무료 메시지 {limit}건을 모두 사용했습니다.",
        "{date} UTC에 번역이 다시 시작됩니다.",
        "월간 한도가 초기화되면 번역이 다시 시작됩니다.",
    ),
    "zh": _copy(
        "已达到免费上限",
        "查看方案",
        "本月 {limit} 条免费消息已用完。",
        "翻译将于 {date} UTC 恢复。",
        "每月额度重置后将恢复翻译。",
    ),
    "zh-tw": _copy(
        "已達到免費上限",
        "查看方案",
        "本月 {limit} 則免費訊息已用完。",
        "翻譯將於 {date} UTC 恢復。",
        "每月額度重置後將恢復翻譯。",
    ),
    "th": _copy(
        "ถึงโควตาฟรีแล้ว",
        "ดูแพ็กเกจ",
        "ใช้ข้อความฟรีเดือนนี้ครบ {limit} ข้อความแล้ว",
        "การแปลจะเริ่มอีกครั้งในวันที่ {date} UTC",
        "การแปลจะเริ่มอีกครั้งเมื่อโควต้ารายเดือนถูกรีเซ็ต",
    ),
    "vi": _copy(
        "Đã hết hạn mức miễn phí",
        "Xem gói",
        "Đã dùng hết {limit} tin miễn phí trong tháng này.",
        "Bản dịch sẽ tiếp tục vào {date} UTC.",
        "Bản dịch sẽ tiếp tục khi hạn mức tháng được đặt lại.",
    ),
    "id": _copy(
        "Kuota gratis habis",
        "Lihat paket",
        "{limit} pesan gratis bulan ini sudah habis.",
        "Terjemahan dilanjutkan pada {date} UTC.",
        "Terjemahan dilanjutkan saat kuota bulanan direset.",
    ),
    "es": _copy(
        "Límite gratis alcanzado",
        "Ver planes",
        "Se usaron los {limit} mensajes gratis de este mes.",
        "La traducción vuelve el {date} UTC.",
        "La traducción vuelve cuando se reinicie la cuota mensual.",
    ),
    "fr": _copy(
        "Limite gratuite atteinte",
        "Voir les offres",
        "Les {limit} messages gratuits de ce mois sont épuisés.",
        "La traduction reprend le {date} UTC.",
        "La traduction reprend au renouvellement du quota mensuel.",
    ),
    "pt": _copy(
        "Limite grátis atingido",
        "Ver planos",
        "As {limit} mensagens grátis deste mês acabaram.",
        "A tradução volta em {date} UTC.",
        "A tradução volta quando a cota mensal for renovada.",
    ),
    "de": _copy(
        "Gratislimit erreicht",
        "Pläne ansehen",
        "Die {limit} Gratisanachrichten dieses Monats sind aufgebraucht.",
        "Die Übersetzung geht am {date} UTC weiter.",
        "Die Übersetzung geht weiter, wenn das Monatslimit zurückgesetzt wird.",
    ),
}

_LANGUAGE_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-sg": "zh",
    "zh-hant": "zh-tw",
    "zh-hk": "zh-tw",
    "zh-mo": "zh-tw",
}


def _has(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text))


def _japanese(text: str) -> bool:
    return _has(_KANA, text)


def _korean(text: str) -> bool:
    return _has(_HANGUL, text) and not _has(_KANA, text)


def _chinese(text: str) -> bool:
    return _has(_CJK, text) and not _has(_KANA, text) and not _has(_HANGUL, text)


def _latin(text: str) -> bool:
    return _has(_LATIN, text)


# 固定訳が無い言語の翻訳結果が、その文字体系で書かれているか見る。
_SCRIPT_CHECKS: dict[str, Callable[[str], bool]] = {
    "ja": _japanese,
    "ko": _korean,
    "zh": _chinese,
    "zh-tw": _chinese,
    "yue": _chinese,
    "th": lambda text: _has(_THAI, text),
    "vi": _latin,
    "id": _latin,
    "ms": _latin,
    "tl": _latin,
    "en": _latin,
    "fr": _latin,
    "de": _latin,
    "es": _latin,
    "pt": _latin,
    "it": _latin,
    "tr": _latin,
    "pl": _latin,
    "nl": _latin,
    "sv": _latin,
    "da": _latin,
    "fi": _latin,
    "no": _latin,
    "cs": _latin,
    "ro": _latin,
    "hu": _latin,
    "sw": _latin,
    "ru": lambda text: _has(_CYRILLIC, text),
    "uk": lambda text: _has(_CYRILLIC, text),
    "ar": lambda text: _has(_ARABIC, text),
    "fa": lambda text: _has(_ARABIC, text),
    "ur": lambda text: _has(_ARABIC, text),
    "hi": lambda text: _has(_DEVANAGARI, text),
    "ne": lambda text: _has(_DEVANAGARI, text),
    "bn": lambda text: _has(_BENGALI, text),
    "ta": lambda text: _has(_TAMIL, text),
    "el": lambda text: _has(_GREEK, text),
    "he": lambda text: _has(_HEBREW, text),
    "my": lambda text: _has(_MYANMAR, text),
    "km": lambda text: _has(_KHMER, text),
    "lo": lambda text: _has(_LAO, text),
    "si": lambda text: _has(_SINHALA, text),
}


def language_key(language: str) -> str:
    """グループ言語コードを固定訳のキーに揃える。"""

    lowered = (language or "").strip().lower().replace("_", "-")
    if lowered.startswith("en"):
        return "en"
    return _LANGUAGE_ALIASES.get(lowered, lowered)


def static_notice_headline(language: str) -> str | None:
    copy = _STATIC_COPY.get(language_key(language))
    return copy.headline if copy else None


def static_notice_button(language: str) -> str | None:
    copy = _STATIC_COPY.get(language_key(language))
    return copy.button if copy else None


def static_notice_body(language: str, limit: int, reset_date: str | None) -> str | None:
    copy = _STATIC_COPY.get(language_key(language))
    if copy is None:
        return None
    return _render_body(copy, limit, reset_date)


def free_limit_body_text(limit: int, reset_date: str | None) -> str:
    """英語の短い本文。固定訳が無いときのフォールバックにも使う。"""

    rendered = static_notice_body("en", limit, reset_date)
    return rendered or ""


def accept_notice_translation(source: str, translated: str, language: str) -> str:
    """翻訳が暴走・誤言語なら英語原文を返す。妥当なら翻訳を返す。"""

    original = (source or "").strip()
    candidate = (translated or "").strip()
    if not original or not candidate or candidate.lower() == original.lower():
        return original
    if _too_long(original, candidate) or _too_many_lines(candidate) or _is_repetitive(candidate):
        return original
    if not _script_matches(candidate, language):
        return original
    return candidate


def build_free_limit_flex_message(
    *,
    headline: str,
    bodies: Sequence[str],
    button_label: str,
    url: str,
) -> dict | None:
    """先頭言語の見出しとボタン、言語ごとの本文を持つバブルを返す。

    URL が Flex の URI アクションに使えないときは None。呼び出し側は従来のテキスト通知に戻す。
    """

    plan_url = (url or "").strip()
    if not _is_flex_uri(plan_url):
        return None

    title = _single_line(_scrub(headline, plan_url), FLEX_HEADLINE_LIMIT) or FREE_LIMIT_HEADLINE
    compact_bodies: list[str] = []
    seen: set[str] = set()
    for body in list(bodies)[:MAX_FLEX_LANGUAGES]:
        compact = _compact_body(_scrub(body, plan_url))
        if not compact or compact in seen:
            continue
        seen.add(compact)
        compact_bodies.append(compact)
    if not compact_bodies:
        return None

    contents: list[dict] = [_headline_component(title)]
    for index, body in enumerate(compact_bodies):
        if index:
            contents.append({"type": "separator"})
        contents.append(_body_component(body))

    return {
        "type": "flex",
        "altText": _alt_text(title, compact_bodies[0]),
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": contents,
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [_plan_button(button_label, plan_url)],
            },
        },
    }


def _render_body(copy: _NoticeCopy, limit: int, reset_date: str | None) -> str:
    amount = f"{limit:,}"
    used = copy.used.format(limit=amount)
    if reset_date:
        resume = copy.resumes_on.format(date=reset_date)
    else:
        resume = copy.resumes_later
    return f"{used}\n{resume}"


def _too_long(source: str, translated: str) -> bool:
    # 元文の数倍を超える出力は、短い通知文としては暴走とみなす。
    return len(translated) > max(len(source) * 3, len(source) + 40)


def _too_many_lines(translated: str) -> bool:
    lines = [line for line in translated.splitlines() if line.strip()]
    return len(lines) > 4


def _is_repetitive(text: str) -> bool:
    """同じ断片が離れてもう一度出る翻訳を落とす。"""

    compact = re.sub(r"\s+", "", text)
    if len(compact) < 16:
        return False
    if re.search(r"(.)\1{5,}", compact):
        return True
    lines = [re.sub(r"\s+", "", line) for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and len(set(lines)) < len(lines):
        return True
    for size in (8, 6, 4):
        if size == 4 and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]", compact):
            return False
        if len(compact) < size * 2:
            continue
        seen: dict[str, int] = {}
        for index in range(len(compact) - size + 1):
            chunk = compact[index : index + size]
            previous = seen.get(chunk)
            if previous is not None and index >= previous + size:
                return True
            if previous is None:
                seen[chunk] = index
    return False


def _script_matches(text: str, language: str) -> bool:
    check = _SCRIPT_CHECKS.get(language_key(language))
    if check is None:
        return True
    return check(text)


def _plan_button(label: str, url: str) -> dict:
    text = _single_line(_scrub(label, url), FLEX_BUTTON_LABEL_LIMIT) or FREE_LIMIT_BUTTON_LABEL
    return {
        "type": "button",
        "style": "primary",
        "height": "sm",
        "action": {
            "type": "uri",
            "label": text,
            "uri": url,
        },
    }


def _headline_component(text: str) -> dict:
    return {
        "type": "text",
        "text": text,
        "weight": "bold",
        "size": "md",
        "wrap": True,
    }


def _body_component(text: str) -> dict:
    return {
        "type": "text",
        "text": text,
        "size": "sm",
        "wrap": True,
    }


def _alt_text(headline: str, body: str) -> str:
    """通知一覧に出る短い文。先頭言語の見出しと本文1行目。"""

    first_line = body.split("\n", 1)[0].strip()
    if not first_line or first_line == headline:
        return _truncate(headline, FLEX_ALT_TEXT_LIMIT)
    separator = " " if _cjk_or_kana(headline) else ". "
    return _truncate(f"{headline}{separator}{first_line}", FLEX_ALT_TEXT_LIMIT)


def _cjk_or_kana(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af\u0e00-\u0e7f]", text))


def _compact_body(text: str) -> str:
    parts = [part.strip() for part in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [part for part in parts if part]
    if not lines:
        return ""
    if len(lines) == 1:
        return _truncate(lines[0], FLEX_BODY_LINE_LIMIT * FLEX_BODY_MAX_LINES)
    first = _truncate(lines[0], FLEX_BODY_LINE_LIMIT)
    rest = _truncate(" ".join(lines[1:]), FLEX_BODY_LINE_LIMIT)
    return f"{first}\n{rest}"


def _single_line(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    return _truncate(collapsed, limit)


def _scrub(text: str, url: str) -> str:
    if not text:
        return ""
    if url and url in text:
        return text.replace(url, " ")
    return text


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"


def _is_flex_uri(url: str) -> bool:
    if not url or len(url) > FLEX_URI_LIMIT:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
