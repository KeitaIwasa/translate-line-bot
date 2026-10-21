from src.domain.services.language_detection_service import LanguageDetectionService

# STG の表示名が文字化けしたまま本文に残る例
_GARBLED_BOT_NAME = "STG \ufffd\u0296\ufffdAI"


def _resolve(text: str, *, group_languages=None, bot_mention_name: str = "") -> str:
    return LanguageDetectionService().resolve_reply_language(
        text,
        group_languages=group_languages,
        bot_mention_name=bot_mention_name,
    )


def test_japanese_mention_is_not_detected_as_korean():
    question = "今の翻訳言語は何?"
    # ボット名を残すと langdetect は韓国語に強く倒れる
    assert LanguageDetectionService().detect(f"通訳AI {question}") == "ko"
    assert _resolve(question) == "ja"
    assert _resolve(f"@通訳AI {question}", bot_mention_name="通訳AI") == "ja"
    assert _resolve(f"@{_GARBLED_BOT_NAME} {question}", bot_mention_name=_GARBLED_BOT_NAME) == "ja"


def test_english_mention_stays_english():
    assert _resolve("What languages are we translating?") == "en"
    assert _resolve("@KOTORI What languages are we translating?", bot_mention_name="KOTORI") == "en"


def test_mixed_japanese_and_english_uses_high_confidence_detection():
    assert _resolve("日本語 and English please") == "en"


def test_emoji_only_and_numbers_only_have_no_reply_language():
    assert _resolve("@通訳AI 😀👍", bot_mention_name="通訳AI") == ""
    assert _resolve("123 456") == ""
    assert _resolve("１２３４５") == ""


def test_low_confidence_han_prefers_group_japanese():
    # 漢字だけだと langdetect は ko に寄る。グループに ja があればそちらを使う
    assert LanguageDetectionService().detect("翻訳言語") == "ko"
    assert _resolve("翻訳言語", group_languages=["en", "ja"]) == "ja"


def test_low_confidence_latin_prefers_group_english():
    assert _resolve("hello", group_languages=["ja", "en"]) == "en"
