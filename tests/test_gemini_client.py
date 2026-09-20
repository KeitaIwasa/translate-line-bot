import json
from datetime import datetime, timezone

import pytest
import requests

from src.domain.models import ContextMessage, TranslationRequest
from src.infra.gemini_errors import GeminiRequestError
from src.infra.gemini_translation import GeminiTranslationAdapter
from src.infra.translation_schema import TRANSLATION_SCHEMA


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


class ErrorResponse:
    status_code = 400

    def raise_for_status(self) -> None:
        raise requests.HTTPError(
            "400 Client Error: Bad Request for url: "
            "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent?key=api-key",
            response=self,
        )


class ErrorSession:
    def post(self, url, params=None, json=None, timeout=None):
        return ErrorResponse()


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

    client = GeminiTranslationAdapter(
        api_key="api-key",
        model="gemini-3.5-flash-lite",
        timeout_seconds=7,
    )

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
    assert payload["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseSchema": TRANSLATION_SCHEMA,
        "thinkingConfig": {"thinkingLevel": "minimal"},
    }


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


def test_translate_truncates_long_texts(monkeypatch, fixed_datetime):
    session = DummySession(response_data=_build_default_response())
    monkeypatch.setattr("infra.gemini_translation.requests.Session", lambda: session)

    client = GeminiTranslationAdapter(api_key="api-key", model="gemini-pro", timeout_seconds=7)

    long_source = "S" * 810
    long_context = "C" * 260

    request = TranslationRequest(
        sender_name="Bob",
        message_text=long_source,
        timestamp=fixed_datetime,
        candidate_languages=["ja"],
        context_messages=[ContextMessage(sender_name="Alice", text=long_context, timestamp=fixed_datetime)],
    )

    client.translate(request)

    payload = session.calls[0]["json"]
    body = json.loads(payload["contents"][0]["parts"][0]["text"])

    assert len(body["source_message"]["text"]) == 800
    assert body["source_message"]["text"].endswith("...")

    assert len(body["context_messages"][0]["text"]) == 250
    assert body["context_messages"][0]["text"].endswith("...")


def test_translate_http_error_does_not_include_api_key_in_exception(monkeypatch, fixed_datetime):
    monkeypatch.setattr("infra.gemini_translation.requests.Session", ErrorSession)
    client = GeminiTranslationAdapter(api_key="api-key", model="gemini-3.5-flash-lite")
    request = TranslationRequest(
        sender_name="Bob",
        message_text="Hello",
        timestamp=fixed_datetime,
        candidate_languages=["ja"],
        context_messages=[],
    )

    with pytest.raises(GeminiRequestError) as exc_info:
        client.translate(request)

    assert "api-key" not in str(exc_info.value)
    assert "generativelanguage.googleapis.com" not in str(exc_info.value)
