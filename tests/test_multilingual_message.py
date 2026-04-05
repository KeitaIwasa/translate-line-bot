from src.domain import models
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.presentation.multilingual_message import build_multilingual_message, dedup_lang_codes


class _Translator:
    def __init__(self, *, raise_error=False):
        self.raise_error = raise_error

    def translate(self, request):
        if self.raise_error:
            raise RuntimeError("translation failed")
        return [models.TranslationResult(lang=lang, text=f"{lang}:{request.message_text}") for lang in request.candidate_languages]


class _Logger:
    def __init__(self):
        self.warning_calls = []

    def warning(self, message, exc_info=False):
        self.warning_calls.append((message, exc_info))


def test_dedup_lang_codes():
    assert dedup_lang_codes(["EN", "ja", "en", "", "JA", "fr"]) == ["en", "ja", "fr"]


def test_build_multilingual_message_fallback_to_english_when_translation_fails():
    logger = _Logger()
    translator = InterfaceTranslationService(_Translator(raise_error=True))
    text = build_multilingual_message(
        base_text="Hello",
        languages=["en", "ja"],
        translator=translator,
        logger=logger,
        warning_log="translation failed",
    )
    assert text == "Hello"
    assert logger.warning_calls and logger.warning_calls[0][0] == "translation failed"


def test_build_multilingual_message_appends_unique_translations():
    logger = _Logger()
    translator = InterfaceTranslationService(_Translator())
    text = build_multilingual_message(
        base_text="Hello",
        languages=["en", "ja", "fr", "ja"],
        translator=translator,
        logger=logger,
        warning_log="translation failed",
    )
    assert text.split("\n\n") == ["Hello", "ja:Hello", "fr:Hello"]
