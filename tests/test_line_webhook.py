import base64
import hashlib
import hmac
import json

import pytest

from line_webhook import LineEvent, SignatureVerificationError, parse_events, verify_signature


def test_parse_events_filters_non_text():
    body = json.dumps(
        {
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
    assert isinstance(event, LineEvent)
    assert event.text == "hello"
    assert event.group_id == "G"
    assert event.user_id == "U"


def test_verify_signature_success_and_failure():
    secret = "topsecret"
    body = "{}"
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()

    verify_signature(secret, body, signature)

    with pytest.raises(SignatureVerificationError):
        verify_signature(secret, body, "invalid")
