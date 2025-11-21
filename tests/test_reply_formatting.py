from reply_formatter import format_translations
from translator.gemini_client import Translation


def test_format_reply_drops_original_and_language_codes():
    translations = [
        Translation(lang="ru", text=" Я люблю тебя "),
        Translation(lang="en", text=" I love you "),
    ]

    result = format_translations(translations)

    assert result == "Я люблю тебя\n\nI love you"
