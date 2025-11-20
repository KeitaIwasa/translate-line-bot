from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import List

try:  # pragma: no cover - optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pylint: disable=broad-except
    pass

from .config import get_settings
from .db import repositories
from .db.neon_client import get_client
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
gemini_service = TranslationService(
    gemini_client=GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
)
db_client = get_client(settings.neon_database_url)


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
            _process_event(evt)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to process event: %s", exc)

    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}


def _extract_body(event) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body


def _process_event(event: LineEvent) -> None:
    timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)
    sender_name = _resolve_sender_name(event)

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

    language_map = repositories.fetch_group_language_preferences(db_client, event.group_id)
    candidate_languages = list(dict.fromkeys([lang for lang in language_map.values() if lang]))

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
            try:
                line_client.reply_text(event.reply_token, reply_text)
            except LineApiError as exc:
                logger.error("LINE reply failed: %s", exc)
    except Exception:
        logger.exception("Translation pipeline failed")
    finally:
        try:
            repositories.insert_message(db_client, record)
        except Exception:
            logger.exception("Failed to persist message")


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
