"""無料枠の上限通知を LINE Flex Message として組み立てる。"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlparse

# 英語をベース原文にする。送信時にグループの設定言語へ翻訳する。
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


def free_limit_body_text(limit: int, reset_date: str | None) -> str:
    """無料枠通知の短い本文。1行目が状況、2行目が再開日。"""

    used = f"This month's {limit:,} free messages are used up."
    if reset_date:
        resume = f"Translation resumes on {reset_date} UTC."
    else:
        resume = "Translation resumes when the monthly quota resets."
    return f"{used}\n{resume}"


def build_free_limit_flex_message(
    *,
    headlines: Sequence[str],
    bodies: Sequence[str],
    button_labels: Sequence[str],
    url: str,
) -> dict | None:
    """言語ごとの見出しと本文、プランページへのボタンを持つバブルを返す。

    URL が Flex の URI アクションに使えないときは None。呼び出し側は従来のテキスト通知に戻す。
    """

    plan_url = (url or "").strip()
    if not _is_flex_uri(plan_url):
        return None

    rows = list(zip(headlines, bodies, button_labels))[:MAX_FLEX_LANGUAGES]
    blocks: list[tuple[str, str]] = []
    labels: list[str] = []
    seen_bodies: set[str] = set()
    for headline, body, label in rows:
        compact_headline = _single_line(_scrub(headline, plan_url), FLEX_HEADLINE_LIMIT)
        compact_body = _compact_body(_scrub(body, plan_url))
        if not compact_headline:
            compact_headline = FREE_LIMIT_HEADLINE
        if not compact_body:
            continue
        if compact_body in seen_bodies:
            # 本文が英語へ戻って他言語と重なるときは、同じ段落を繰り返さない
            labels.append(label)
            continue
        seen_bodies.add(compact_body)
        blocks.append((compact_headline, compact_body))
        labels.append(label)

    if not blocks:
        return None

    headline, first_body = blocks[0]
    # 見出しは先頭言語の1つだけにして、本文だけを言語ごとに積む
    contents: list[dict] = [_headline_component(headline)]
    for index, (_block_headline, body) in enumerate(blocks):
        if index:
            contents.append({"type": "separator"})
        contents.append(_body_component(body))

    return {
        "type": "flex",
        "altText": _alt_text(headline, first_body),
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
                "contents": [_plan_button(labels, plan_url)],
            },
        },
    }


def _plan_button(labels: Sequence[str], url: str) -> dict:
    return {
        "type": "button",
        "style": "primary",
        "height": "sm",
        "action": {
            "type": "uri",
            "label": _button_label(labels),
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


def _button_label(labels: Sequence[str]) -> str:
    """収まれば「View plans / プランを見る」のように並べ、超えたら先頭の言語だけにする。"""

    cleaned: list[str] = []
    seen: set[str] = set()
    for label in labels:
        text = _single_line(label, FLEX_BUTTON_LABEL_LIMIT)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if not cleaned:
        return FREE_LIMIT_BUTTON_LABEL
    joined = " / ".join(cleaned)
    if len(joined) <= FLEX_BUTTON_LABEL_LIMIT:
        return joined
    return cleaned[0]


def _alt_text(headline: str, body: str) -> str:
    """通知一覧に出る短い文。先頭言語の見出しと本文1行目。"""

    first_line = body.split("\n", 1)[0].strip()
    if not first_line or first_line == headline:
        return _truncate(headline, FLEX_ALT_TEXT_LIMIT)
    return _truncate(f"{headline}. {first_line}", FLEX_ALT_TEXT_LIMIT)


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
