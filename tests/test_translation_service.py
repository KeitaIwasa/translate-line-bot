from datetime import datetime, timezone

import pytest

from translator.gemini_client import ContextMessage as GeminiContextMessage
from translator.gemini_client import Translation
from translator.service import TranslationService


class DummyGeminiClient:
    def __init__(self, return_value):
        self.return_value = return_value
        self.calls = []

    def translate(self, source_message, context_messages, target_languages):
        self.calls.append(
            {
                "source": source_message,
                "context": list(context_messages),
                "targets": target_languages,
            }
        )
        return self.return_value


def _context():
    now = datetime.now(tz=timezone.utc)
    return [GeminiContextMessage(sender_name="Alice", text="Hi", timestamp=now)]


def test_translate_filters_detected_language(monkeypatch):
    dummy_response = [Translation(lang="ja", text="こんにちは"), Translation(lang="fr", text="salut")]
    dummy_client = DummyGeminiClient(dummy_response)
    service = TranslationService(dummy_client)

    monkeypatch.setattr("translator.service.detect_language", lambda _: "en")

    timestamp = datetime.now(tz=timezone.utc)
    result = service.translate(
        sender_name="Bob",
        message_text="Hello",
        timestamp=timestamp,
        context_messages=_context(),
        candidate_languages=["en", "ja", "fr", "ja"],
    )

    assert result == dummy_response
    assert dummy_client.calls[0]["targets"] == ["ja", "fr"], "Should drop detected lang and deduplicate order"


def test_translate_returns_empty_when_no_targets(monkeypatch):
    dummy_client = DummyGeminiClient(return_value=[Translation(lang="ja", text="hi")])
    service = TranslationService(dummy_client)
    monkeypatch.setattr("translator.service.detect_language", lambda _: "ja")

    timestamp = datetime.now(tz=timezone.utc)
    result = service.translate(
        sender_name="Bob",
        message_text="こんにちは",
        timestamp=timestamp,
        context_messages=_context(),
        candidate_languages=["ja"],
    )

    assert result == []
    assert dummy_client.calls == []
