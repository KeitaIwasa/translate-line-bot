from datetime import datetime, timezone

import pytest

from src.domain.models import ContextMessage, TranslationResult
from src.domain.services.language_detection_service import (
    LanguageDetectionService,
    _CLAUSE_SPLIT_RE,
    _linguistic_stats,
)
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
    assert _targets_for("100元", ["ja", "en"]) == []
    assert _targets_for("３，０００円", ["ja", "en"]) == []


def test_currency_word_alone_is_translatable():
    # 円・元は数値に付かないときは語。翻訳自体は抑止しない。
    detector = LanguageDetectionService()
    assert detector.has_translatable_text("円") is True
    assert detector.has_translatable_text("元") is True
    assert detector.has_translatable_text("円高") is True
    assert detector.has_translatable_text("3,000円") is False
    assert _targets_for("円", ["ja", "en"])
    assert _targets_for("元", ["ja", "en"])


def test_single_language_messages_still_drop_the_source_language():
    assert _targets_for("こんにちは、元気ですか？", ["ja", "en", "th"]) == ["en", "th"]
    assert _targets_for("Please confirm by tonight.", ["ja", "en"]) == ["ja"]
    assert _targets_for("LINEで送った", ["ja", "en"]) == ["en"]


def test_english_spanish_mix_keeps_both_group_languages():
    # 全文の detect は一方に寄る。文節で en と es の両方が十分な長さなら両方残す。
    text = "Let's meet at the station tomorrow morning. Nos vemos en la estación mañana por la mañana."
    detected = detect_language(text).lower()
    assert detected.startswith("en") or detected.startswith("es")
    assert _targets_for(text, ["en", "es"]) == ["en", "es"]
    assert _targets_for(text, ["en", "es", "ja"]) == ["en", "es", "ja"]


def test_russian_ukrainian_mix_keeps_both_group_languages():
    text = "Сегодня мы обсудим план на неделю. Сьогодні ми обговоримо план на тиждень."
    assert _targets_for(text, ["ru", "uk"]) == ["ru", "uk"]


def test_french_vietnamese_mix_keeps_both_group_languages():
    text = (
        "Je vais au marché cet après-midi pour acheter des légumes. "
        "Tôi sẽ đi chợ vào chiều nay để mua rau."
    )
    assert _targets_for(text, ["fr", "vi"]) == ["fr", "vi"]


def test_single_spanish_word_in_english_is_not_mixed():
    # 単語1つの外国語では混在にせず、detect が選んだ言語だけを落とす
    assert detect_language("See you mañana").lower().startswith("es")
    assert _targets_for("See you mañana", ["en", "es"]) == ["en"]


def test_pure_spanish_is_not_mixed():
    text = "Por favor confirma la reserva para el viernes por la noche."
    assert detect_language(text).lower().startswith("es")
    assert _targets_for(text, ["en", "es"]) == ["en"]


def test_pure_russian_and_ukrainian_are_not_mixed():
    russian = "Сегодня хорошая погода и мы пойдём гулять вечером."
    ukrainian = "Сьогодні гарна погода і ми підемо гуляти ввечері."
    assert detect_language(russian).lower().startswith("ru")
    assert detect_language(ukrainian).lower().startswith("uk")
    assert _targets_for(russian, ["ru", "uk"]) == ["uk"]
    assert _targets_for(ukrainian, ["ru", "uk"]) == ["ru"]


def test_names_urls_and_acronyms_do_not_count_as_same_script_mix():
    assert _targets_for("Please check LINE tonight and reply when you can.", ["en", "es"]) == ["es"]
    assert _targets_for("Maria Garcia will join the meeting tomorrow morning.", ["en", "es"]) == ["es"]
    assert _targets_for(
        "See the details at https://example.com/docs and confirm the time.",
        ["en", "es"],
    ) == ["es"]


def test_arabic_script_mix_keeps_each_group_language():
    # アラビア文字の ar/fa/ur は文節ごとの判定で混在にする
    text = (
        "سوف نعقد الاجتماع في المكتب غدا صباحا لمناقشة الخطة الأسبوعية مع الفريق. "
        "جلسه را فردا صبح در دفتر برگزار می کنیم تا برنامه هفتگی را با تیم بررسی کنیم."
    )
    assert _targets_for(text, ["ar", "fa"]) == ["ar", "fa"]
    urdu = (
        "سوف نعقد الاجتماع في المكتب غدا صباحا لمناقشة الخطة الأسبوعية مع الفريق. "
        "ہم کل صبح دفتر میں میٹنگ کریں گے تاکہ ہفتہ وار منصوبے پر تمام اراکین کے ساتھ بات کریں."
    )
    assert _targets_for(urdu, ["ar", "ur"]) == ["ar", "ur"]


def test_devanagari_mix_keeps_each_group_language():
    text = (
        "हम कल सुबह कार्यालय में बैठक करेंगे और साप्ताहिक योजना पर चर्चा करेंगे. "
        "आपण उद्या सकाळी कार्यालयात बैठक घेऊ आणि साप्ताहिक योजनेवर चर्चा करू."
    )
    assert _targets_for(text, ["hi", "mr"]) == ["hi", "mr"]
    nepali = (
        "हम कल सुबह कार्यालय में बैठक करेंगे और साप्ताहिक योजना पर चर्चा करेंगे. "
        "हामी भोलि बिहान कार्यालयमा बैठक गर्नेछौं र साप्ताहिक योजनाबारे सबै सदस्यहरूसँग छलफल गर्नेछौं."
    )
    assert _targets_for(nepali, ["hi", "ne"]) == ["hi", "ne"]


def test_han_mix_keeps_japanese_and_chinese():
    # 句点で分かれた十分な長さの文節だけを、日本語と中国語として数える
    text = (
        "明日の朝に事務所で来週の予定について全員で打ち合わせをします。"
        "我们明天上午在办公室开会讨论下周的工作计划请大家提前阅读资料。"
    )
    assert _targets_for(text, ["ja", "zh-cn"]) == ["ja", "zh-cn"]
    traditional = (
        "明日の朝に事務所で来週の予定について全員で打ち合わせをします。"
        "我們明天上午在辦公室開會討論下週的工作計畫請大家提前閱讀資料。"
    )
    assert _targets_for(traditional, ["ja", "zh-tw"]) == ["ja", "zh-tw"]


def test_pure_same_script_language_is_not_mixed():
    arabic = "سوف نعقد الاجتماع في المكتب غدا صباحا لمناقشة الخطة الأسبوعية مع الفريق."
    hindi = "हम कल सुबह कार्यालय में बैठक करेंगे और साप्ताहिक योजना पर चर्चा करेंगे."
    japanese = "明日の朝に事務所で来週の予定について全員で打ち合わせをします。"
    chinese = "我们明天上午在办公室开会讨论下周的工作计划请大家提前阅读资料。"
    assert _targets_for(arabic, ["ar", "fa"]) == ["fa"]
    assert _targets_for(hindi, ["hi", "mr"]) == ["mr"]
    assert _targets_for(japanese, ["ja", "zh-cn"]) == ["zh-cn"]
    assert _targets_for(chinese, ["ja", "zh-cn"]) == ["ja"]


def test_period_after_digit_splits_sentences_but_decimal_does_not():
    # 数字の直後のピリオドは文の区切り。小数点（digit.digit）だけは残す。
    parts = [
        part.strip()
        for part in _CLAUSE_SPLIT_RE.split(
            "The meeting is in room 3. La reunión será en la sala principal."
        )
        if part and part.strip()
    ]
    assert parts == [
        "The meeting is in room 3",
        "La reunión será en la sala principal",
    ]
    decimal = [
        part.strip()
        for part in _CLAUSE_SPLIT_RE.split("The total price is 3.14 dollars today.")
        if part and part.strip()
    ]
    assert decimal == ["The total price is 3.14 dollars today"]

    text = (
        "The meeting is in conference room 3. "
        "La reunión será en la sala principal del edificio."
    )
    assert _targets_for(text, ["en", "es"]) == ["en", "es"]
    decimal_mix = (
        "The total price is 3.14 dollars today. "
        "La reunión será en la sala principal del edificio."
    )
    assert _targets_for(decimal_mix, ["en", "es"]) == ["en", "es"]


def test_urls_emails_and_mentions_do_not_add_other_scripts():
    detector = LanguageDetectionService()
    bare = "Please read this page before the meeting tomorrow morning. see ru.wikipedia.org/wiki/Москва"
    linked = "Please read this page before the meeting tomorrow morning. https://ru.wikipedia.org/wiki/Москва"
    mention = "Please read this note before the meeting tomorrow morning @Москва"
    email = "Please read this note before the meeting tomorrow morning user@пример.ru"
    for text in (bare, linked, mention, email):
        assert detector.is_mixed_language(text, ["en", "ru"]) is False
        # 英語の本文だけが残るので、検出言語の英語を翻訳先から外す
        assert _targets_for(text, ["en", "ru"]) == ["ru"]


def test_single_foreign_script_name_is_not_mixed():
    # 英語文の中の人名1語は、別文字体系でも混在にしない
    detector = LanguageDetectionService()
    text = "Please invite Мария to the meeting tomorrow."
    assert detector.is_mixed_language(text, ["en", "ru"]) is False
    assert detect_language(text).lower().startswith("en")
    assert _targets_for(text, ["en", "ru"]) == ["ru"]


def test_secondary_script_share_or_clause_is_mixed():
    detector = LanguageDetectionService()
    # 2語以上かつ文字の約15%以上なら、文の途中でも混在
    embedded = "Please read приходи завтра утром before the meeting."
    assert detector.is_mixed_language(embedded, ["en", "ru"]) is True
    assert _targets_for(embedded, ["en", "ru"]) == ["en", "ru"]
    # 独立した節なら、短い日本語でも日本語＋英語の混在のまま
    clause = "I'll join the meeting tomorrow at 3pm if the room is free. 了解です。"
    assert detector.is_mixed_language(clause, ["en", "ja"]) is True
    assert _targets_for(clause, ["en", "ja"]) == ["en", "ja"]
    japanese_and_english = "明日のmeetingはonlineでやりますか? Please confirm by tonight."
    assert detector.is_mixed_language(japanese_and_english, ["ja", "en"]) is True


def test_glued_acronym_keeps_following_japanese():
    # LINE だけを除き、接している日本語は文節の文字数に残す
    glued = "LINEで明日の朝に事務所で来週の予定について全員で打ち合わせをします"
    letters, words = _linguistic_stats(glued)
    assert letters >= 20
    assert words >= 4
    assert _linguistic_stats("LINE") == (0, 0)
    text = (
        "LINEで明日の朝に事務所で来週の予定について全員で打ち合わせをします。"
        "我们明天上午在办公室开会讨论下周的工作计划请大家提前阅读资料。"
    )
    assert _targets_for(text, ["ja", "zh-cn"]) == ["ja", "zh-cn"]
