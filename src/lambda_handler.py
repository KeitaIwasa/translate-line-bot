from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:  # pragma: no cover - optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pylint: disable=broad-except
    pass

from .config import get_settings
from .db import repositories
from .db.neon_client import get_client
from .language_preferences import LanguagePreferenceService
from .line_api import LineApiClient, LineApiError
from .line_webhook import LineEvent, SignatureVerificationError, parse_events, verify_signature
from .translator.gemini_client import (
    ContextMessage as GeminiContextMessage,
    GeminiClient,
    Translation,
)
from .translator.service import TranslationService

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

line_client = LineApiClient(settings.line_channel_access_token)
gemini_translation_client = GeminiClient(
    api_key=settings.gemini_api_key,
    model=settings.gemini_model,
    timeout_seconds=settings.gemini_timeout_seconds,
)
gemini_service = TranslationService(gemini_client=gemini_translation_client)
language_pref_service = LanguagePreferenceService(
    api_key=settings.gemini_api_key,
    model=settings.gemini_model,
    timeout_seconds=settings.gemini_timeout_seconds,
)
db_client = get_client(settings.neon_database_url)

GROUP_PROMPT_MESSAGE = (
    "I'm a multilingual translation bot. Please tell me the languages you want to translate to.\n"
    "我是一个多语言翻译机器人。请告诉我你想要翻译成哪些语言。\n"
    "多言語翻訳ボットです。翻訳したい言語を教えてください。\n"
    "ฉันเป็นบอทแปลหลายภาษา กรุณาบอกฉันว่าคุณต้องการแปลเป็นภาษาใดบ้าง\n"
    "\nex) English, 中文, 日本語, ไทย"
)
DIRECT_GREETING = (
    "Thanks for adding me! Please invite me into a group so I can help with multilingual translation."
)


def lambda_handler(event, _context):
    headers = event.get("headers") or {}
    signature = headers.get("X-Line-Signature") or headers.get("x-line-signature")

    try:
        body = _extract_body(event)
        verify_signature(settings.line_channel_secret, body, signature)
    except SignatureVerificationError as exc:
        logger.warning("Signature verification failed: %s", exc)
        return {"statusCode": 403, "body": json.dumps({"message": "Forbidden"})}

    events = parse_events(body)

    for evt in events:
        try:
            _dispatch_event(evt)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to process event: %s", exc)

    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}


def _extract_body(event) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body


def _dispatch_event(event: LineEvent) -> None:
    if event.event_type == "message":
        _handle_message_event(event)
    elif event.event_type == "postback":
        _handle_postback_event(event)
    elif event.event_type == "join":
        _handle_join_event(event)
    elif event.event_type == "memberJoined":
        _handle_member_joined_event(event)
    elif event.event_type == "follow":
        _handle_follow_event(event)


def _handle_message_event(event: LineEvent) -> None:
    if not event.reply_token:
        return

    if event.sender_type == "user" and (not event.group_id or event.group_id == event.user_id):
        line_client.reply_text(event.reply_token, DIRECT_GREETING)
        return

    if settings.bot_user_id and event.user_id == settings.bot_user_id:
        return

    repositories.ensure_group_member(db_client, event.group_id, event.user_id)

    timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
    sender_name = _resolve_sender_name(event)

    language_map = repositories.fetch_group_language_preferences(db_client, event.group_id)
    candidate_languages = list({lang for langs in language_map.values() for lang in langs if lang})

    user_languages = language_map.get(event.user_id) or []
    if not user_languages:
        logger.info(
            "user has no language preferences yet; attempting enrollment",
            extra={"group_id": event.group_id, "user_id": event.user_id},
        )
        if _attempt_language_enrollment(event):
            return

    context_messages = repositories.fetch_recent_messages(
        db_client,
        event.group_id,
        settings.bot_user_id,
        settings.max_context_messages,
    )
    gemini_context = [
        GeminiContextMessage(sender_name=msg.sender_name, text=msg.text, timestamp=msg.timestamp)
        for msg in context_messages
    ]

    record = repositories.MessageRecord(
        group_id=event.group_id,
        user_id=event.user_id,
        sender_name=sender_name,
        text=event.text,
        timestamp=timestamp,
    )
    try:
        translations = _invoke_translation_with_retry(
            sender_name=sender_name,
            message_text=event.text,
            timestamp=timestamp,
            context=gemini_context,
            candidate_languages=candidate_languages,
        )

        if translations:
            reply_text = _format_reply(event.text, translations)
            line_client.reply_text(event.reply_token, reply_text)
    except Exception:
        logger.exception("Translation pipeline failed")
    finally:
        try:
            repositories.insert_message(db_client, record)
        except Exception:
            logger.exception("Failed to persist message")


def _attempt_language_enrollment(event: LineEvent) -> bool:
    logger.info(
        "Analyzing language preferences",
        extra={"group_id": event.group_id, "user_id": event.user_id, "text": event.text[:120]},
    )
    try:
        result = language_pref_service.analyze(event.text)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to analyze language preferences: %s", exc)
        return False

    if not result:
        logger.info("Language analysis returned no result", extra={"user_id": event.user_id})
        return False

    supported = result.supported_languages
    unsupported = result.unsupported_languages
    logger.info(
        "Language analysis outcome",
        extra={
            "user_id": event.user_id,
            "supported": [lang.code for lang in supported],
            "unsupported": [lang.code for lang in unsupported],
        },
    )

    messages = []
    if unsupported:
        messages.append({"type": "text", "text": _format_unsupported_message(unsupported)})

    if not supported:
        if messages and event.reply_token:
            line_client.reply_messages(event.reply_token, messages)
        return True

    confirm_payload = _encode_postback_payload(
        {
            "kind": "language_confirm",
            "action": "confirm",
            "group_id": event.group_id,
            "user_id": event.user_id,
            "languages": [
                {"code": lang.code, "name": lang.english_name or lang.primary_name}
                for lang in supported
            ],
            "texts": {
                "completed": _textset_to_dict(result.completed_text),
                "cancel": _textset_to_dict(result.cancel_text),
            },
        }
    )

    cancel_payload = _encode_postback_payload(
        {
            "kind": "language_confirm",
            "action": "cancel",
            "group_id": event.group_id,
            "user_id": event.user_id,
            "texts": {"cancel": _textset_to_dict(result.cancel_text)},
        }
    )

    confirm_text = (result.confirm_text.primary or _build_simple_confirm_text(supported))[:400]
    template_message = {
        "type": "template",
        "altText": "Confirm interpretation languages",
        "template": {
            "type": "confirm",
            "text": confirm_text,
            "actions": [
                {"type": "postback", "label": f"🆗 {result.confirm_label}", "data": confirm_payload},
                {"type": "postback", "label": f"↩️ {result.cancel_label}", "data": cancel_payload},
            ],
        },
    }

    messages.append(template_message)
    if event.reply_token:
        line_client.reply_messages(event.reply_token, messages)
    repositories.record_language_prompt(db_client, event.group_id, event.user_id)
    logger.info(
        "Language enrollment prompt sent",
        extra={"group_id": event.group_id, "user_id": event.user_id, "prompted_langs": [lang.code for lang in supported]},
    )
    return True


def _handle_postback_event(event: LineEvent) -> None:
    if not event.postback_data or not event.reply_token:
        return

    payload = _decode_postback_payload(event.postback_data)
    if not payload or payload.get("kind") != "language_confirm":
        logger.debug("Ignoring unrelated postback", extra={"data": event.postback_data})
        return

    action = payload.get("action")
    if action == "confirm":
        langs = payload.get("languages") or []
        tuples = [
            (item.get("code", ""), item.get("name", ""))
            for item in langs
            if item.get("code")
        ]
        if not (event.group_id and event.user_id):
            return
        repositories.replace_user_languages(db_client, event.group_id, event.user_id, tuples)
        text = _build_text_from_payload(payload.get("texts", {}).get("completed"))
        line_client.reply_text(event.reply_token, text)
        logger.info(
            "Language preferences saved",
            extra={"group_id": event.group_id, "user_id": event.user_id, "languages": [code for code, _ in tuples]},
        )
    elif action == "cancel":
        text = _build_text_from_payload(payload.get("texts", {}).get("cancel"))
        line_client.reply_text(event.reply_token, text)
        logger.info("Language enrollment cancelled", extra={"group_id": event.group_id, "user_id": event.user_id})


def _handle_join_event(event: LineEvent) -> None:
    if not (event.group_id and event.reply_token):
        return
    repositories.reset_group_language_settings(db_client, event.group_id)
    line_client.reply_text(event.reply_token, GROUP_PROMPT_MESSAGE)


def _handle_member_joined_event(event: LineEvent) -> None:
    if not (event.group_id and event.reply_token):
        return

    joined_names: List[str] = []
    for user_id in event.joined_user_ids:
        if not user_id:
            continue
        repositories.ensure_group_member(db_client, event.group_id, user_id)
        name = line_client.get_display_name("group", event.group_id, user_id)
        if name:
            joined_names.append(name)

    prefix = "、".join(joined_names) if joined_names else "New members"
    message = f"{prefix} さん、ようこそ！\n" + GROUP_PROMPT_MESSAGE
    line_client.reply_text(event.reply_token, message)


def _handle_follow_event(event: LineEvent) -> None:
    if not event.reply_token:
        return
    line_client.reply_text(event.reply_token, DIRECT_GREETING)


def _resolve_sender_name(event: LineEvent) -> str:
    if event.user_id:
        name = line_client.get_display_name(event.sender_type, event.group_id, event.user_id)
        if name:
            return name
    return event.user_id or "Unknown"


def _invoke_translation_with_retry(
    sender_name: str,
    message_text: str,
    timestamp: datetime,
    context: List[GeminiContextMessage],
    candidate_languages: List[str],
) -> List[Translation]:
    if not candidate_languages:
        return []

    last_error: Exception | None = None
    for attempt in range(settings.translation_retry):
        try:
            return gemini_service.translate(
                sender_name=sender_name,
                message_text=message_text,
                timestamp=timestamp,
                context_messages=context,
                candidate_languages=candidate_languages,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Gemini translation failed (attempt %s/%s)",
                attempt + 1,
                settings.translation_retry,
            )
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    logger.error("Gemini translation failed after retries")
    if last_error:
        raise last_error
    return []


def _format_reply(original_text: str, translations: List[Translation]) -> str:
    lines = [original_text.strip()]
    for item in translations:
        lines.append(f"[{item.lang.lower()}] {item.text.strip()}")
    joined = "\n".join(filter(None, lines))
    return joined[:5000]


def _format_unsupported_message(languages) -> str:
    messages = []
    for lang in languages:
        primary = lang.primary_name or lang.english_name or lang.code
        english = lang.english_name or lang.code
        thai = lang.thai_name or lang.english_name or lang.code
        messages.append(
            f"{primary}には通訳対応できません。\n"
            f"I cannot provide interpretation for {english}.\n"
            f"ฉันไม่สามารถให้บริการล่ามสำหรับ{thai}ได้"
        )
    return "\n\n".join(messages)


def _build_simple_confirm_text(languages) -> str:
    names = [lang.primary_name or lang.english_name or lang.code for lang in languages]
    joined = "、".join(filter(None, names))
    if joined:
        return f"{joined}の翻訳を有効にしますか？"
    return "翻訳したい言語を確認してもよろしいですか？"


def _encode_postback_payload(payload: Dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return f"langpref={encoded}"


def _decode_postback_payload(data: str) -> Optional[Dict]:
    if not data.startswith("langpref="):
        return None
    token = data.split("=", 1)[1]
    padding = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(token + padding).decode("utf-8")
        return json.loads(decoded)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to decode postback payload", extra={"data": data})
        return None


def _textset_to_dict(text_set) -> Dict[str, str]:
    primary = text_set.primary or text_set.english or text_set.thai or ""
    return {"primary": primary}


def _build_text_from_payload(payload: Optional[Dict]) -> str:
    if not payload:
        return "設定を取り消しました。再度、翻訳したい言語をすべて教えてください。"
    primary = payload.get("primary")
    return primary or "設定を取り消しました。再度、翻訳したい言語をすべて教えてください。"
