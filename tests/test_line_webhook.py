import base64
import hashlib
import hmac
import json

import pytest

from src.presentation.line_webhook_parser import SignatureVerificationError, parse_events, verify_signature
from src.domain import models


def test_parse_events_filters_non_text():
    body = json.dumps(
        {
            "destination": "BOT",
            "events": [
                {
                    "type": "message",
                    "replyToken": "abc",
                    "timestamp": 1732060800000,
                    "message": {"type": "text", "text": "hello"},
                    "source": {"type": "group", "groupId": "G", "userId": "U"},
                },
                {
                    "type": "message",
                    "replyToken": "abc",
                    "timestamp": 1732060800000,
                    "message": {"type": "image"},
                    "source": {"type": "group", "groupId": "G", "userId": "U"},
                },
                {
                    "type": "follow",
                    "source": {"type": "group", "groupId": "G"},
                },
            ]
        }
    )

    events = parse_events(body)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, models.MessageEvent)
    assert event.text == "hello"
    assert event.group_id == "G"
    assert event.user_id == "U"
    assert event.destination == "BOT"
    assert event.mentionees == []


def test_parse_events_extracts_mention_metadata():
    body = json.dumps(
        {
            "destination": "BOT",
            "events": [
                {
                    "type": "message",
                    "replyToken": "abc",
                    "timestamp": 1732060800000,
                    "message": {
                        "type": "text",
                        "text": "@通訳AI - การตีความ AI アップグレード",
                        "mention": {
                            "mentionees": [
                                {
                                    "index": 0,
                                    "length": 20,
                                    "type": "user",
                                    "userId": "BOT",
                                    "isSelf": True,
                                }
                            ]
                        },
                    },
                    "source": {"type": "group", "groupId": "G", "userId": "U"},
                }
            ]
        }
    )

    events = parse_events(body)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, models.MessageEvent)
    assert event.destination == "BOT"
    assert event.mentionees == [
        models.Mentionee(index=0, length=20, mention_type="user", user_id="BOT", is_self=True)
    ]


def test_parse_events_extracts_mention_metadata_without_is_self():
    body = json.dumps(
        {
            "destination": "BOT",
            "events": [
                {
                    "type": "message",
                    "replyToken": "abc",
                    "timestamp": 1732060800000,
                    "message": {
                        "type": "text",
                        "text": "@通訳AI - การตีความ AI プラン変更",
                        "mention": {
                            "mentionees": [
                                {
                                    "index": 0,
                                    "length": 20,
                                    "type": "user",
                                    "userId": "BOT",
                                }
                            ]
                        },
                    },
                    "source": {"type": "group", "groupId": "G", "userId": "U"},
                }
            ]
        }
    )

    events = parse_events(body)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, models.MessageEvent)
    assert event.destination == "BOT"
    assert event.mentionees == [
        models.Mentionee(index=0, length=20, mention_type="user", user_id="BOT", is_self=False)
    ]


def test_verify_signature_success_and_failure():
    secret = "topsecret"
    body = "{}"
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()

    verify_signature(secret, body, signature)

    with pytest.raises(SignatureVerificationError):
        verify_signature(secret, body, "invalid")


def test_parse_leave_event():
    body = json.dumps(
        {
            "events": [
                {
                    "type": "leave",
                    "timestamp": 1732060800000,
                    "source": {"type": "group", "groupId": "G", "userId": "U"},
                }
            ]
        }
    )

    events = parse_events(body)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, models.LeaveEvent)
    assert event.group_id == "G"
    assert event.reply_token is None


def test_parse_member_left_event():
    body = json.dumps(
        {
            "events": [
                {
                    "type": "memberLeft",
                    "timestamp": 1732060800000,
                    "source": {"type": "group", "groupId": "G", "userId": "U"},
                    "left": {"members": [{"type": "user", "userId": "U1"}, {"type": "user", "userId": "U2"}]},
                }
            ]
        }
    )

    events = parse_events(body)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, models.MemberLeftEvent)
    assert event.group_id == "G"
    assert event.left_user_ids == ["U1", "U2"]
