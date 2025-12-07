import os

import pytest
from dotenv import load_dotenv

from src.app.handlers.message_handler import MessageHandler, UNKNOWN_INSTRUCTION_JA
from src.domain import models
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.language_detection_service import LanguageDetectionService
from src.domain.services.translation_service import TranslationService
from src.infra.gemini_translation import GeminiTranslationAdapter


load_dotenv()


class DummyLineClient:
    def reply_text(self, *_args, **_kwargs):
        return None

    def reply_messages(self, *_args, **_kwargs):
        return None

    def get_display_name(self, *_args, **_kwargs):
        return None


class DummyLangPrefService:
    def analyze(self, _text: str):
        return None


class DummyCommandRouter:
    def decide(self, _text: str):
        return models.CommandDecision(action="unknown", instruction_language="", ack_text="")


class DummyTranslationService:
    def translate(self, *_args, **_kwargs):
        return []


class CollapsingTranslationService:
    """改行が失われた翻訳結果を返すスタブ。"""

    def translate(self, request: models.TranslationRequest, *_args, **_kwargs):
        return [
            models.TranslationResult(
                lang=request.candidate_languages[0],
                text=(
                    "If you want to interact with this bot by mentioning it, "
                    "please mention it again and instruct one of the following: "
                    "- Change language settings - Usage instructions - Stop translation"
                ),
            )
        ]


class DummyRepo:
    def ensure_group_member(self, *_args, **_kwargs):
        return None

    def fetch_group_languages(self, *_args, **_kwargs):
        return []

    def fetch_recent_messages(self, *_args, **_kwargs):
        return []

    def insert_message(self, *_args, **_kwargs):
        return None

    def record_language_prompt(self, *_args, **_kwargs):
        return None

    def try_complete_group_languages(self, *_args, **_kwargs):
        return False

    def try_cancel_language_prompt(self, *_args, **_kwargs):
        return False

    def reset_group_language_settings(self, *_args, **_kwargs):
        return None

    def record_bot_joined_at(self, *_args, **_kwargs):
        return None

    def fetch_bot_joined_at(self, *_args, **_kwargs):
        return None

    def add_group_languages(self, *_args, **_kwargs):
        return None

    def remove_group_languages(self, *_args, **_kwargs):
        return None

    def set_translation_enabled(self, *_args, **_kwargs):
        return None

    def is_translation_enabled(self, *_args, **_kwargs):
        return True

    def increment_usage(self, *_args, **_kwargs):
        return 0

    def get_usage(self, *_args, **_kwargs):
        return 0

    def get_subscription_status(self, *_args, **_kwargs):
        return "active"

    def upsert_subscription(self, *_args, **_kwargs):
        return None

    def update_subscription_status(self, *_args, **_kwargs):
        return None


@pytest.fixture(scope="module")
def gemini_adapter():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY is not set; live Gemini test skipped")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return GeminiTranslationAdapter(api_key=api_key, model=model, timeout_seconds=20)


def test_language_detection_basic():
    detector = LanguageDetectionService()
    assert detector.detect("Hello, how are you?").startswith("en")
    assert detector.detect("こんにちは").startswith("ja")


def test_unknown_instruction_translated_to_detected_language(gemini_adapter):
    translation_service = TranslationService(gemini_adapter)
    interface_translation = InterfaceTranslationService(gemini_adapter)
    language_detector = LanguageDetectionService()

    handler = MessageHandler(
        line_client=DummyLineClient(),
        translation_service=translation_service,
        interface_translation=interface_translation,
        language_detector=language_detector,
        language_pref_service=DummyLangPrefService(),
        command_router=DummyCommandRouter(),
        repo=DummyRepo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=2,
        bot_mention_name="bot",
    )

    # translate unknown-instruction guidance into English
    result = handler._build_unknown_response("en")

    assert result
    assert result != UNKNOWN_INSTRUCTION_JA
    # Heuristic: translated text should contain ASCII letters predominantly (avoid original Japanese)
    assert any(ch.isascii() and ch.isalpha() for ch in result)


def test_unknown_instruction_keeps_bullet_newlines():
    line_client = DummyLineClient()
    collapsing_translator = CollapsingTranslationService()

    handler = MessageHandler(
        line_client=line_client,
        translation_service=TranslationService(collapsing_translator),
        interface_translation=InterfaceTranslationService(collapsing_translator),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(),
        command_router=DummyCommandRouter(),
        repo=DummyRepo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )

    result = handler._build_unknown_response("en")

    assert result.split("\n- ")[0].strip().endswith("following:")
    assert result.count("\n- ") == 3


def test_language_settings_invalid_operation_returns_unknown_instruction():
    class RecordingLineClient(DummyLineClient):
        def __init__(self):
            self.last_text = None

        def reply_text(self, _reply_token, text):
            self.last_text = text
            return None

    class InvalidOpCommandRouter:
        def decide(self, _text: str):
            return models.CommandDecision(
                action="language_settings",
                operation="invalid",
                instruction_language="",
                ack_text="",
            )

    line_client = RecordingLineClient()
    handler = MessageHandler(
        line_client=line_client,
        translation_service=DummyTranslationService(),
        interface_translation=InterfaceTranslationService(DummyTranslationService()),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(),
        command_router=InvalidOpCommandRouter(),
        repo=DummyRepo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text="@bot do something",  # command_text is provided separately
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler._handle_command(event, "unsupported")

    assert line_client.last_text == UNKNOWN_INSTRUCTION_JA
