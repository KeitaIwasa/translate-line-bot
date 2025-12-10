from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List, Optional

from ..domain import models

logger = logging.getLogger(__name__)


class SignatureVerificationError(RuntimeError):
    pass


def verify_signature(channel_secret: str, body: str, signature: Optional[str]) -> None:
    if not signature:
        raise SignatureVerificationError("Missing X-Line-Signature header")

    mac = hmac.new(channel_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256)
    digest = base64.b64encode(mac.digest()).decode("utf-8")
    if not hmac.compare_digest(digest, signature):
        raise SignatureVerificationError("Invalid signature")


def parse_events(body: str) -> List[models.BaseEvent]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc

    events_raw = payload.get("events", [])
    events: List[models.BaseEvent] = []

    for event in events_raw:
        event_type = event.get("type")
        source = event.get("source", {})
        sender_type = source.get("type", "user")
        group_id = _resolve_group_id(source)
        user_id = source.get("userId")
        reply_token = event.get("replyToken")

        if event_type == "message":
            message = event.get("message", {})
            if message.get("type") != "text":
                continue
            if not group_id or not reply_token:
                continue
            events.append(
                models.MessageEvent(
                    event_type="message",
                    reply_token=reply_token,
                    group_id=group_id,
                    user_id=user_id,
                    sender_type=sender_type,
                    text=message.get("text", ""),
                    timestamp=event.get("timestamp", 0),
                )
            )
        elif event_type == "postback":
            data = event.get("postback", {}).get("data")
            if not data or not reply_token or not group_id:
                continue
            events.append(
                models.PostbackEvent(
                    event_type="postback",
                    reply_token=reply_token,
                    group_id=group_id,
                    user_id=user_id,
                    sender_type=sender_type,
                    data=data,
                    timestamp=event.get("timestamp", 0),
                )
            )
        elif event_type == "join":
            if not reply_token or not group_id:
                continue
            events.append(
                models.JoinEvent(
                    event_type="join",
                    reply_token=reply_token,
                    group_id=group_id,
                    user_id=user_id,
                    sender_type=sender_type,
                    timestamp=event.get("timestamp", 0),
                )
            )
        elif event_type == "memberJoined":
            joined = event.get("joined", {}).get("members", [])
            joined_ids = [member.get("userId") for member in joined if member.get("userId")]
            if not reply_token or not group_id or not joined_ids:
                continue
            events.append(
                models.MemberJoinedEvent(
                    event_type="memberJoined",
                    reply_token=reply_token,
                    group_id=group_id,
                    user_id=user_id,
                    sender_type=sender_type,
                    joined_user_ids=joined_ids,
                    timestamp=event.get("timestamp", 0),
                )
            )
        elif event_type == "follow":
            if not reply_token:
                continue
            events.append(
                models.FollowEvent(
                    event_type="follow",
                    reply_token=reply_token,
                    group_id=group_id,
                    user_id=user_id,
                    sender_type=sender_type,
                    timestamp=event.get("timestamp", 0),
                )
            )
        elif event_type == "leave":
            if not group_id:
                continue
            events.append(
                models.LeaveEvent(
                    event_type="leave",
                    reply_token=reply_token,
                    group_id=group_id,
                    user_id=user_id,
                    sender_type=sender_type,
                    timestamp=event.get("timestamp", 0),
                )
            )
    return events


def _resolve_group_id(source: Dict[str, Any]) -> Optional[str]:
    return source.get("groupId") or source.get("roomId") or source.get("userId")
