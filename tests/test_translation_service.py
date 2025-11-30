from datetime import datetime, timezone

import pytest

from src.domain.models import ContextMessage, TranslationResult
from src.domain.services.translation_service import TranslationService


class DummyTranslator:
    def __init__(self, return_value):
        self.return_value = return_value
        self.calls = []

    def translate(self, request):
        self.calls.append(request)
        return self.return_value


def _context():
    now = datetime.now(tz=timezone.utc)
    return [ContextMessage(sender_name="Alice", text="Hi", timestamp=now)]


def test_translate_filters_detected_language(monkeypatch):
    dummy_response = [TranslationResult(lang="ja", text="こんにちは"), TranslationResult(lang="fr", text="salut")]
    dummy_client = DummyTranslator(dummy_response)
    service = TranslationService(dummy_client)

    monkeypatch.setattr("src.domain.services.translation_service.detect_language", lambda _: "en")

    timestamp = datetime.now(tz=timezone.utc)
    result = service.translate(
        sender_name="Bob",
        message_text="Hello",
        timestamp=timestamp,
        context_messages=_context(),
        candidate_languages=["en", "ja", "fr", "ja"],
    )

    assert result == dummy_response
    request = dummy_client.calls[0]
    assert request.candidate_languages == ["ja", "fr"], "Should drop detected lang and deduplicate order"


def test_translate_returns_empty_when_no_targets(monkeypatch):
    dummy_client = DummyTranslator(return_value=[TranslationResult(lang="ja", text="hi")])
    service = TranslationService(dummy_client)
    monkeypatch.setattr("src.domain.services.translation_service.detect_language", lambda _: "ja")

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
