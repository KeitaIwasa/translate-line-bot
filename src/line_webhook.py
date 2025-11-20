from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LineEvent:
    reply_token: str
    group_id: str
    user_id: str
    sender_type: str
    text: str
    timestamp: int


class SignatureVerificationError(RuntimeError):
    pass


def verify_signature(channel_secret: str, body: str, signature: Optional[str]) -> None:
    if not signature:
        raise SignatureVerificationError("Missing X-Line-Signature header")

    mac = hmac.new(channel_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256)
    digest = base64.b64encode(mac.digest()).decode("utf-8")
    if not hmac.compare_digest(digest, signature):
        raise SignatureVerificationError("Invalid signature")


def parse_events(body: str) -> List[LineEvent]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc

    events_raw = payload.get("events", [])
    events: List[LineEvent] = []

    for event in events_raw:
        if event.get("type") != "message":
            continue
        message = event.get("message", {})
        if message.get("type") != "text":
            continue
        source = event.get("source", {})
        group_id = source.get("groupId") or source.get("roomId") or source.get("userId")
        if not group_id:
            logger.debug("Skipping event without group/room/user id", extra={"event": event})
            continue
        user_id = source.get("userId") or ""
        reply_token = event.get("replyToken")
        if not reply_token:
            continue
        events.append(
            LineEvent(
                reply_token=reply_token,
                group_id=group_id,
                user_id=user_id,
                sender_type=source.get("type", "user"),
                text=message.get("text", ""),
                timestamp=event.get("timestamp", 0),
            )
        )

    return events
