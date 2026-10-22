"""システム案内の翻訳が、数字と日付を変えていないこと。"""

from src.domain import models
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.notice_tokens import (
    FIXED_NOTICE_TRANSLATIONS,
    fallback_notice_text,
    notice_tokens_preserved,
)


SOURCE = "The Free plan includes 50 messages per month. The next reset is on 2026-11-01 (UTC)."


def test_fullwidth_digits_match_the_source_number():
    assert notice_tokens_preserved(SOURCE, "Freeプランは月５０メッセージです。次のリセットは2026-11-01（UTC）です。")


def test_man_and_grouped_thousands_do_not_match_fifty():
    assert not notice_tokens_preserved(SOURCE, "月5万メッセージ。次のリセットは2026-11-01です。")
    assert not notice_tokens_preserved(SOURCE, "月50,000メッセージ。次のリセットは2026-11-01です。")
    assert not notice_tokens_preserved(SOURCE, "月５０，０００メッセージ。次のリセットは2026-11-01です。")


def test_localized_dates_match_only_when_they_are_the_same_day():
    assert notice_tokens_preserved(SOURCE, "50 messages. 次のリセットは2026年11月1日です。")
    assert notice_tokens_preserved(SOURCE, "50 messages. The next reset is on November 1, 2026.")
    assert notice_tokens_preserved(SOURCE, "50 messages. 2026/11/01")
    assert not notice_tokens_preserved(SOURCE, "50 messages. 2026年12月1日")
    assert not notice_tokens_preserved(SOURCE, "50 messages. 11/01/2026")


def test_echoed_source_cannot_mask_a_wrong_translation():
    wrong = f"{SOURCE}\n月5万メッセージ。次のリセットは2026年12月1日です。"
    assert not notice_tokens_preserved(SOURCE, wrong)
    correct = f"{SOURCE}\n無料プランは月50メッセージです。次のリセットは2026年11月1日です。"
    assert notice_tokens_preserved(SOURCE, correct)


def test_extra_number_or_date_is_a_mismatch():
    assert not notice_tokens_preserved(SOURCE, "月50メッセージです。あと3件。2026年11月1日")
    assert not notice_tokens_preserved(SOURCE, "月50メッセージです。2026年11月1日。2026年12月1日")


def test_grouped_source_number_accepts_the_same_integer_only():
    source = "Standard 4,000/month."
    assert notice_tokens_preserved(source, "スタンダードは4000件です。")
    assert notice_tokens_preserved(source, "スタンダードは4,000件です。")
    assert not notice_tokens_preserved(source, "スタンダードは40,000件です。")


class _Scripted:
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def translate(self, request):
        self.calls += 1
        text = self._replies.pop(0)
        return [models.TranslationResult(lang=lang, text=text) for lang in request.candidate_languages]


def test_interface_translation_retries_once_then_uses_fixed_or_english():
    translator = _Scripted(
        [
            "月5万メッセージ。2026-11-01",
            "月５０メッセージ。2026年11月1日",
        ]
    )
    service = InterfaceTranslationService(translator)
    result = service.translate(SOURCE, ["ja"])
    assert translator.calls == 2
    assert "50" in result[0].text or "５０" in result[0].text
    assert "5万" not in result[0].text

    translator = _Scripted(
        [
            "月5万メッセージ。2026-11-01",
            "月50,000メッセージ。2026-11-01",
        ]
    )
    service = InterfaceTranslationService(translator)
    FIXED_NOTICE_TRANSLATIONS[SOURCE] = {"ja": "無料プランは月50メッセージです。次のリセットは2026-11-01（UTC）です。"}
    try:
        result = service.translate(SOURCE, ["ja"])
        assert translator.calls == 2
        assert result[0].text == FIXED_NOTICE_TRANSLATIONS[SOURCE]["ja"]
        assert fallback_notice_text("no fixed text", "ja") == "no fixed text"
    finally:
        FIXED_NOTICE_TRANSLATIONS.pop(SOURCE, None)


def test_interface_translation_retries_when_the_echo_hides_a_mistranslation():
    translator = _Scripted(
        [
            f"{SOURCE}\n月5万メッセージ。2026-11-01",
            "月50メッセージ。2026年11月1日",
        ]
    )
    service = InterfaceTranslationService(translator)
    result = service.translate(SOURCE, ["ja"])
    assert translator.calls == 2
    assert "5万" not in result[0].text
    assert "50" in result[0].text


def test_interface_translation_keeps_a_fullwidth_number_without_retry():
    translator = _Scripted(["月５０メッセージ。2026-11-01"])
    service = InterfaceTranslationService(translator)
    result = service.translate(SOURCE, ["ja"])
    assert translator.calls == 1
    assert result[0].text == "月５０メッセージ。2026-11-01"
