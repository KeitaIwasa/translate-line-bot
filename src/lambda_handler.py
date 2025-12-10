from __future__ import annotations

import base64
import json
import logging
from typing import Dict

try:  # pragma: no cover - optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pylint: disable=broad-except
    pass

from .app.bootstrap import build_dispatcher
from .config import get_settings
from .presentation.line_webhook_parser import (
    SignatureVerificationError,
    parse_events,
    verify_signature,
)

settings = get_settings()
dispatcher = build_dispatcher()

logger = logging.getLogger(__name__)


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
    logger.info(
        "Parsed LINE webhook events | count=%s types=%s",
        len(events),
        [evt.event_type for evt in events],
    )
    if not events:
        logger.warning("No dispatchable events found in payload")

    for evt in events:
        try:
            dispatcher.dispatch(evt)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to process event: %s", exc)

    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}


def _extract_body(event) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body
