"""案内・コマンド返信を LINE Flex と平文フォールバックへ組み立てる。

個別の Flex JSON をメッセージごとに書かず、GuidanceMessage から同じ形の
バブル（見出し、本文、URI / メッセージ / postback ボタン、クイックリプライ）を作る。
Flex がサイズ上限を超える、または送信に失敗したときは、URL を本文から分けた平文に戻す。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Sequence

# LINE Messaging API の上限。文字数は UTF-16 コードユニットで数える。
ALT_TEXT_LIMIT = 400
FLEX_BUTTON_LABEL_LIMIT = 40
QUICK_REPLY_LABEL_LIMIT = 20
URI_LIMIT = 1000
MESSAGE_ACTION_TEXT_LIMIT = 300
POSTBACK_DATA_LIMIT = 300
TEXT_COMPONENT_LIMIT = 2000
PLAIN_TEXT_LIMIT = 5000
FLEX_PAYLOAD_LIMIT = 50_000
MAX_FLEX_BUTTONS = 4
MAX_QUICK_REPLIES = 13
MAX_REPLY_MESSAGES = 5

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_BIDI_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_RTL_RE = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")
_LTR_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_TRAILING_URL_PUNCT = ".,;:)]}"


@dataclass(frozen=True)
class RichButton:
    """Flex のボタン、またはクイックリプライ1件。

    uri / text / data のうち入っているものを使う。優先順は uri、postback、message。
    """

    label: str
    uri: str = ""
    text: str = ""
    data: str = ""
    style: str = "primary"


@dataclass(frozen=True)
class GuidanceMessage:
    """リッチ表示にする案内。body はバブル本文。plain_text は送信失敗時の原文。"""

    body: str
    title: str = ""
    alt_text: str = ""
    buttons: tuple[RichButton, ...] = ()
    quick_replies: tuple[RichButton, ...] = ()
    plain_text: str = ""


@dataclass(frozen=True)
class RenderedGuidance:
    """Flex と、失敗時に送る平文メッセージ列。"""

    flex: dict | None
    plain_messages: tuple[dict, ...]
    omit_reason: str = ""


def utf16_len(text: str) -> int:
    """LINE の文字数上限に合わせ、UTF-16 コードユニット数を返す。"""

    return len((text or "").encode("utf-16-le")) // 2


def truncate_utf16(text: str, limit: int, *, ellipsis: bool = False) -> str:
    """上限以内に切る。ellipsis のときは末尾を … にして上限を超えない。"""

    if limit <= 0:
        return ""
    raw = text or ""
    if utf16_len(raw) <= limit:
        return raw
    budget = limit - 1 if ellipsis else limit
    if budget <= 0:
        return "…" if ellipsis else ""
    pieces: list[str] = []
    used = 0
    for char in raw:
        size = utf16_len(char)
        if used + size > budget:
            break
        pieces.append(char)
        used += size
    trimmed = "".join(pieces).rstrip()
    if ellipsis:
        return f"{trimmed}…"
    return trimmed


def split_urls(text: str) -> tuple[str, list[str]]:
    """本文から http(s) URL を取り出す。URL が無ければ原文をそのまま返す。"""

    source = text or ""
    urls: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        trimmed = raw.rstrip(_TRAILING_URL_PUNCT)
        if not trimmed:
            return raw
        urls.append(trimmed)
        # 末尾の句読点は本文に残す
        return raw[len(trimmed) :]

    cleaned = _URL_RE.sub(_replace, source)
    if not urls:
        return source, []
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:)\]])", r"\1", cleaned)
    cleaned = cleaned.strip()
    # URL の直前に残った「こちら:」「here:」を文として読める形に戻す。
    cleaned = re.sub(r"(\bhere)\s*[:：]\s*$", r"\1.", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*[:：]\s*$", "", cleaned)
    return cleaned.strip(), urls


def visible_reply_text(messages: Sequence[dict] | None) -> str:
    """テストとログ用に、Flex 本文と平文を人が読む順でつなぐ。"""

    parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "flex":
            parts.append(flex_body_text(message))
            continue
        text = message.get("text")
        if text:
            parts.append(str(text))
    return "\n".join(part for part in parts if part)


def flex_body_text(message: dict) -> str:
    """Flex バブル本文のテキストを、段落の空行を保って返す。"""

    contents = ((message or {}).get("contents") or {}).get("body") or {}
    paragraphs: list[str] = []
    for component in contents.get("contents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("type") == "text":
            paragraphs.append(str(component.get("text") or ""))
            continue
        if component.get("type") != "box":
            continue
        chunks = [
            str(child.get("text") or "")
            for child in component.get("contents") or []
            if isinstance(child, dict) and child.get("type") == "text"
        ]
        paragraphs.append("".join(chunks))
    return "\n\n".join(part for part in paragraphs if part)


def flex_action_count(message: dict) -> int:
    """Flex のボタンとクイックリプライの数。"""

    if not isinstance(message, dict) or message.get("type") != "flex":
        return 0
    count = 0
    footer = ((message.get("contents") or {}).get("footer") or {})
    for component in footer.get("contents") or []:
        if isinstance(component, dict) and component.get("type") == "button" and component.get("action"):
            count += 1
    for item in ((message.get("quickReply") or {}).get("items") or []):
        if isinstance(item, dict) and item.get("action"):
            count += 1
    return count


def render_guidance(message: GuidanceMessage, *, brand: str = "KOTORI") -> RenderedGuidance:
    """GuidanceMessage を Flex と平文へ描画する。上限を超える Flex は作らない。"""

    plain_source = (message.plain_text or message.body or "").strip() or (message.title or "").strip()
    plain_messages = tuple(_plain_messages(plain_source or brand))
    body, _urls = split_urls(message.body or "")
    body = body.strip()
    buttons = _fit_buttons(message.buttons, label_limit=FLEX_BUTTON_LABEL_LIMIT)
    if any(_uri_too_long(button.uri) for button in message.buttons if button.uri):
        return RenderedGuidance(flex=None, plain_messages=plain_messages, omit_reason="uri_too_long")

    quick_replies = _fit_buttons(message.quick_replies, label_limit=QUICK_REPLY_LABEL_LIMIT)[:MAX_QUICK_REPLIES]
    if not body and not buttons:
        return RenderedGuidance(flex=None, plain_messages=plain_messages, omit_reason="empty")

    flex = _flex_message(
        title=(message.title or "").strip(),
        body=body,
        alt_text=_alt_text(message, body),
        buttons=buttons[:MAX_FLEX_BUTTONS],
        quick_replies=quick_replies,
        brand=brand.strip() or "KOTORI",
    )
    payload_size = len(json.dumps(flex, ensure_ascii=False).encode("utf-8"))
    if payload_size > FLEX_PAYLOAD_LIMIT:
        return RenderedGuidance(flex=None, plain_messages=plain_messages, omit_reason="payload_too_large")
    return RenderedGuidance(flex=flex, plain_messages=plain_messages, omit_reason="")


def deliver_reply(line, reply_token: str, message: GuidanceMessage, *, logger: logging.Logger) -> list[dict]:
    """Flex を返信し、失敗したら平文へ戻す。送ったメッセージ列を返す。"""

    rendered = render_guidance(message)
    if rendered.flex is not None:
        try:
            line.reply_messages(reply_token, [rendered.flex])
            return [rendered.flex]
        except Exception:
            logger.warning("Flex reply failed; falling back to plain text", exc_info=True)
    elif rendered.omit_reason:
        logger.info("Flex guidance omitted | reason=%s", rendered.omit_reason)
    line.reply_messages(reply_token, list(rendered.plain_messages))
    return list(rendered.plain_messages)


def deliver_push(line, to: str, message: GuidanceMessage, *, logger: logging.Logger) -> list[dict]:
    """Flex を push し、失敗したら平文へ戻す。送ったメッセージ列を返す。"""

    rendered = render_guidance(message)
    if rendered.flex is not None:
        try:
            line.push_messages(to, [rendered.flex])
            return [rendered.flex]
        except Exception:
            logger.warning("Flex push failed; falling back to plain text", exc_info=True)
    elif rendered.omit_reason:
        logger.info("Flex guidance omitted | reason=%s", rendered.omit_reason)
    line.push_messages(to, list(rendered.plain_messages))
    return list(rendered.plain_messages)


def _plain_messages(text: str) -> list[dict]:
    """URL を別吹き出しに分け、返信5件以内に収める。"""

    body, urls = split_urls(text)
    body = body.strip()
    messages: list[dict] = []
    if body:
        messages.append({"type": "text", "text": body[:PLAIN_TEXT_LIMIT]})
    pending = [url for url in urls if url not in (body or "")]
    for url in pending:
        if len(messages) < MAX_REPLY_MESSAGES - 1 or not messages:
            messages.append({"type": "text", "text": url[:PLAIN_TEXT_LIMIT]})
            continue
        # 5件目に残りの URL をまとめる。単独 URL の吹き出しより落ちないことを優先する。
        current = messages[-1]["text"]
        extra = f"{current}\n{url}"
        messages[-1] = {"type": "text", "text": extra[:PLAIN_TEXT_LIMIT]}
    if not messages:
        messages.append({"type": "text", "text": "KOTORI"})
    return messages[:MAX_REPLY_MESSAGES]


def _alt_text(message: GuidanceMessage, body: str) -> str:
    explicit = _clean_preview(message.alt_text)
    preview = explicit or _first_line(body) or _clean_preview(message.title) or "KOTORI"
    return truncate_utf16(preview, ALT_TEXT_LIMIT, ellipsis=True) or "KOTORI"


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = _clean_preview(line)
        if cleaned:
            return cleaned
    return ""


def _clean_preview(text: str) -> str:
    without_urls = _URL_RE.sub("", text or "")
    without_bidi = _BIDI_RE.sub("", without_urls)
    return " ".join(without_bidi.split())


def _flex_message(
    *,
    title: str,
    body: str,
    alt_text: str,
    buttons: Sequence[RichButton],
    quick_replies: Sequence[RichButton],
    brand: str,
) -> dict:
    header_contents: list[dict] = [
        {
            "type": "text",
            "text": truncate_utf16(brand, 40),
            "color": "#E8F6EF",
            "weight": "bold",
            "size": "xs",
            "wrap": True,
        }
    ]
    if title:
        header_contents.append(
            {
                "type": "text",
                "text": truncate_utf16(title, TEXT_COMPONENT_LIMIT),
                "color": "#FFFFFF",
                "weight": "bold",
                "size": "md",
                "wrap": True,
            }
        )
    bubble: dict = {
        "type": "bubble",
        "size": "giga",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#0F766E",
            "paddingAll": "12px",
            "spacing": "xs",
            "contents": header_contents,
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "14px",
            "contents": _paragraph_components(body),
        },
    }
    if buttons:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "12px",
            "contents": [_button_component(button, index) for index, button in enumerate(buttons)],
        }
    flex: dict = {"type": "flex", "altText": alt_text, "contents": bubble}
    if quick_replies:
        flex["quickReply"] = {"items": [_quick_reply_item(button) for button in quick_replies]}
    return flex


def _paragraph_components(body: str) -> list[dict]:
    paragraphs = [part for part in re.split(r"\n\n+", body.strip()) if part]
    components: list[dict] = []
    for paragraph in paragraphs:
        chunks = _split_chunk(paragraph, TEXT_COMPONENT_LIMIT)
        if len(chunks) == 1:
            components.append(_text_component(chunks[0]))
            continue
        components.append(
            {
                "type": "box",
                "layout": "vertical",
                "spacing": "none",
                "contents": [_text_component(chunk) for chunk in chunks],
            }
        )
    if not components:
        components.append(_text_component("KOTORI"))
    return components


def _text_component(text: str) -> dict:
    component = {
        "type": "text",
        "text": text,
        "size": "sm",
        "color": "#222222",
        "wrap": True,
    }
    if _is_rtl(text):
        component["align"] = "end"
    return component


def _is_rtl(text: str) -> bool:
    stripped = _BIDI_RE.sub("", text or "")
    if (text or "").startswith(("\u200f", "\u202b", "\u202e")):
        return True
    rtl = len(_RTL_RE.findall(stripped))
    ltr = len(_LTR_RE.findall(stripped))
    return rtl > ltr and rtl > 0


def _split_chunk(text: str, limit: int) -> list[str]:
    if utf16_len(text) <= limit:
        return [text]
    lines = text.split("\n")
    if len(lines) == 1:
        return _hard_split(text, limit)
    chunks: list[str] = []
    current = ""
    for line in lines:
        pieces = _hard_split(line, limit) if utf16_len(line) > limit else [line]
        for piece in pieces:
            candidate = piece if not current else f"{current}\n{piece}"
            if current and utf16_len(candidate) > limit:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks or [truncate_utf16(text, limit)]


def _hard_split(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for char in text:
        size = utf16_len(char)
        if current and used + size > limit:
            chunks.append("".join(current))
            current = [char]
            used = size
            continue
        current.append(char)
        used += size
    if current:
        chunks.append("".join(current))
    return chunks


def _fit_buttons(buttons: Sequence[RichButton], *, label_limit: int) -> list[RichButton]:
    fitted: list[RichButton] = []
    for button in buttons:
        action_kind = _action_kind(button)
        if action_kind is None:
            continue
        label = truncate_utf16((button.label or "").strip(), label_limit)
        if not label:
            label = "Open" if action_kind == "uri" else "Send"
            label = truncate_utf16(label, label_limit)
        if not label:
            continue
        fitted.append(
            RichButton(
                label=label,
                uri=button.uri.strip() if action_kind == "uri" else "",
                text=truncate_utf16(button.text, MESSAGE_ACTION_TEXT_LIMIT) if action_kind == "message" else "",
                data=button.data if action_kind == "postback" else "",
                style=button.style if button.style in {"primary", "secondary", "link"} else "primary",
            )
        )
    return fitted


def _action_kind(button: RichButton) -> str | None:
    uri = (button.uri or "").strip()
    if uri:
        if not _valid_uri(uri) or _uri_too_long(uri):
            return None
        return "uri"
    data = button.data or ""
    if data:
        if utf16_len(data) > POSTBACK_DATA_LIMIT:
            return None
        return "postback"
    text = (button.text or "").strip()
    if text:
        return "message"
    return None


def _uri_too_long(uri: str) -> bool:
    return utf16_len(uri or "") > URI_LIMIT


def _valid_uri(uri: str) -> bool:
    lowered = uri.lower()
    return lowered.startswith(("https://", "http://", "line://", "tel:"))


def _button_component(button: RichButton, index: int) -> dict:
    style = button.style
    if index > 0 and style == "primary":
        style = "secondary"
    return {
        "type": "button",
        "style": style,
        "height": "sm",
        "action": _action_object(button),
    }


def _quick_reply_item(button: RichButton) -> dict:
    return {"type": "action", "action": _action_object(button)}


def _action_object(button: RichButton) -> dict:
    if button.uri:
        return {"type": "uri", "label": button.label, "uri": button.uri}
    if button.data:
        return {"type": "postback", "label": button.label, "data": button.data}
    return {"type": "message", "label": button.label, "text": button.text}
