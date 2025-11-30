import json
from datetime import datetime, timezone

import pytest

from src.domain.models import ContextMessage, TranslationRequest
from src.infra.gemini_translation import GeminiTranslationAdapter


class DummyResponse:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self) -> None:  # pragma: no cover - nothing to raise
        return None

    def json(self) -> dict:
        return self._data


class DummySession:
    def __init__(self, response_data: dict):
        self._response = response_data
        self.calls = []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "json": json,
                "timeout": timeout,
            }
        )
        return DummyResponse(self._response)


def _build_default_response():
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "translations": [
                                        {"lang": "ja", "text": "こんにちは"},
                                        {"lang": "fr", "text": "salut"},
                                        {"lang": "de", "text": "hallo"},
                                    ]
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }


@pytest.fixture
def fixed_datetime():
    return datetime(2025, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


def test_translate_builds_payload_and_filters_translations(monkeypatch, fixed_datetime):
    session = DummySession(response_data=_build_default_response())
    monkeypatch.setattr("infra.gemini_translation.requests.Session", lambda: session)

    client = GeminiTranslationAdapter(api_key="api-key", model="gemini-pro", timeout_seconds=7)

    request = TranslationRequest(
        sender_name="Bob",
        message_text="Hello",
        timestamp=fixed_datetime,
        candidate_languages=["ja", "fr"],
        context_messages=[ContextMessage(sender_name="Alice", text="Hi", timestamp=fixed_datetime)],
    )

    translations = client.translate(request)

    assert translations[0].lang == "ja" and translations[0].text == "こんにちは"
    assert translations[1].lang == "fr" and translations[1].text == "salut"

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"].endswith(":generateContent")
    assert call["params"] == {"key": "api-key"}
    assert call["timeout"] == 7

    payload = call["json"]
    body = json.loads(payload["contents"][0]["parts"][0]["text"])
    assert body["source_message"]["text"] == "Hello"
    assert body["context_messages"][0]["sender_name"] == "Alice"
    assert body["context_messages"][0]["timestamp"] == fixed_datetime.strftime("%Y-%m-%d %H:%M:%S")
    assert body["target_languages"] == ["ja", "fr"]


def test_translate_skips_request_when_no_targets(monkeypatch, fixed_datetime):
    session = DummySession(response_data=_build_default_response())
    monkeypatch.setattr("infra.gemini_translation.requests.Session", lambda: session)

    client = GeminiTranslationAdapter(api_key="api-key", model="gemini-pro", timeout_seconds=7)
    request = TranslationRequest(
        sender_name="Bob",
        message_text="Hello",
        timestamp=fixed_datetime,
        candidate_languages=[],
        context_messages=[],
    )

    translations = client.translate(request)

    assert translations == []
    assert session.calls == []
