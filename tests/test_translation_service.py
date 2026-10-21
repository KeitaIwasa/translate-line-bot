from datetime import datetime, timezone

import pytest

from src.domain.models import ContextMessage, TranslationResult
from src.domain.services.translation_service import TranslationService, detect_language


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


def _targets_for(text: str, languages: list[str]) -> list[str]:
    dummy_client = DummyTranslator(return_value=[TranslationResult(lang="ja", text="ok")])
    service = TranslationService(dummy_client)
    service.translate(
        sender_name="Bob",
        message_text=text,
        timestamp=datetime.now(tz=timezone.utc),
        context_messages=_context(),
        candidate_languages=languages,
    )
    if not dummy_client.calls:
        return []
    return list(dummy_client.calls[0].candidate_languages)


def test_ja_en_mix_translates_into_every_group_language():
    # langdetect はこの文を en とだけ判定し、従来は英語訳を落としていた
    text = "明日のmeetingはonlineでやりますか? Please confirm by tonight."
    assert detect_language(text).lower().startswith("en")
    assert _targets_for(text, ["ja", "en"]) == ["ja", "en"]
    assert _targets_for(text, ["ja", "en", "th"]) == ["ja", "en", "th"]


def test_english_dominant_mix_keeps_english_and_japanese():
    text = "I'll join the meeting tomorrow at 3pm if the room is free. 了解です。"
    assert detect_language(text).lower().startswith("en")
    assert _targets_for(text, ["en", "ja"]) == ["en", "ja"]


def test_mix_with_language_outside_the_group_still_covers_group_languages():
    # タイ語はグループ言語ではない。日本語優勢でも日本語訳を落とさない。
    text = "明日の会議はオンラインですか？ สวัสดีครับ"
    assert detect_language(text).lower().startswith("ja")
    assert _targets_for(text, ["ja", "en"]) == ["ja", "en"]


def test_emoji_only_does_not_translate():
    assert _targets_for("😊", ["ja", "en"]) == []
    assert _targets_for("😊😂🎉", ["ja", "en"]) == []


def test_numbers_only_does_not_translate():
    assert _targets_for("12345", ["ja", "en"]) == []
    assert _targets_for("12:30", ["ja", "en"]) == []
    assert _targets_for("100%", ["ja", "en"]) == []
    assert _targets_for("3,000円", ["ja", "en"]) == []


def test_single_language_messages_still_drop_the_source_language():
    assert _targets_for("こんにちは、元気ですか？", ["ja", "en", "th"]) == ["en", "th"]
    assert _targets_for("Please confirm by tonight.", ["ja", "en"]) == ["ja"]
    assert _targets_for("LINEで送った", ["ja", "en"]) == ["en"]
