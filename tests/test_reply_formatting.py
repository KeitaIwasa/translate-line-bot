from reply_formatter import format_translations
from src.domain.models import TranslationResult


def test_format_reply_drops_original_and_language_codes():
    translations = [
        TranslationResult(lang="ru", text=" Я люблю тебя "),
        TranslationResult(lang="en", text=" I love you "),
    ]

    result = format_translations(translations)

    assert result == "Я люблю тебя\n\nI love you"
