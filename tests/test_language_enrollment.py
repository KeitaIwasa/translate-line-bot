import os

from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.domain.services.translation_service import TranslationService
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.language_detection_service import LanguageDetectionService


os.environ.setdefault("LINE_CHANNEL_SECRET", "dummy")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
os.environ.setdefault("GEMINI_API_KEY", "dummy")
os.environ.setdefault("NEON_DATABASE_URL", "postgres://dummy")


class DummyLineClient:
    def __init__(self):
        self.sent = {}

    def reply_messages(self, reply_token, messages):
        self.sent["reply_token"] = reply_token
        self.sent["messages"] = messages

    def reply_text(self, reply_token, text):
        self.sent["reply_token"] = reply_token
        self.sent["messages"] = [{"type": "text", "text": text}]

    def get_display_name(self, *_args, **_kwargs):
        return None


class DummyTranslationService(TranslationService):
    def __init__(self):
        pass

    def translate(self, *args, **kwargs):
        return []


class DummyRepo:
    def __init__(self):
        self.recorded = False

    def ensure_group_member(self, *args, **kwargs):
        return None

    def fetch_group_languages(self, *_args, **_kwargs):
        return []

    def fetch_recent_messages(self, *_args, **_kwargs):
        return []

    def insert_message(self, *_args, **_kwargs):
        return None

    def record_language_prompt(self, *args, **kwargs):
        self.recorded = True

    def try_complete_group_languages(self, *args, **kwargs):
        return False

    def try_cancel_language_prompt(self, *args, **kwargs):
        return False

    def reset_group_language_settings(self, *args, **kwargs):
        return None

    def record_bot_joined_at(self, *args, **kwargs):
        return None

    def fetch_bot_joined_at(self, *args, **kwargs):
        return None

    def add_group_languages(self, *args, **kwargs):
        return None

    def remove_group_languages(self, *args, **kwargs):
        return None

    def set_translation_enabled(self, *args, **kwargs):
        return None

    def is_translation_enabled(self, *args, **kwargs):
        return True


class DummyCommandRouter:
    def decide(self, text: str):
        return models.CommandDecision(action="unknown", instruction_language="ja", ack_text="")


class DummyInterfaceTranslation(InterfaceTranslationService):
    def __init__(self):
        class _Translator:
            def translate(self, *args, **kwargs):
                return []

        super().__init__(_Translator())

    def translate(self, *args, **kwargs):
        return []


class DummyLangPrefService:
    def __init__(self, result):
        self.result = result

    def analyze(self, _text: str):
        return self.result


def test_language_enrollment_ignores_unsupported_in_confirm():
    fake_supported = [
        models.LanguageChoice(code="ja", name="日本語"),
        models.LanguageChoice(code="ar", name="アラビア語"),
    ]
    fake_unsupported = [models.LanguageChoice(code="sa", name="サンスクリット語")]

    fake_result = models.LanguagePreference(
        supported=fake_supported,
        unsupported=fake_unsupported,
        confirm_label="OK",
        cancel_label="Cancel",
        confirm_text="",
        cancel_text="",
        completion_text="",
        primary_language="ja",
    )

    line = DummyLineClient()
    repo = DummyRepo()
    handler = MessageHandler(
        line_client=line,
        translation_service=DummyTranslationService(),
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(fake_result),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        translation_retry=1,
        bot_mention_name="bot",
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="reply-token",
        timestamp=0,
        text="日本語、アラビア語、絵文字、サンスクリット語",
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler._attempt_language_enrollment(event)

    messages = line.sent["messages"]
    assert messages[0]["type"] == "text"
    assert "サンスクリット" in messages[0]["text"]

    template = messages[1]["template"]
    assert template["type"] == "confirm"
    assert template["text"] == "日本語、アラビア語の翻訳を有効にしますか？"

    confirm_payload = _decode_payload(template["actions"][0]["data"])
    langs = [item["code"] for item in confirm_payload["languages"]]
    assert langs == ["ja", "ar"]
    assert confirm_payload["completion_text"].startswith("日本語、アラビア語")
    assert confirm_payload["primary_language"] == "ja"
    assert repo.recorded is True


def test_language_enrollment_uses_instruction_language_texts():
    fake_supported = [
        models.LanguageChoice(code="en", name="English"),
        models.LanguageChoice(code="ja", name="Japanese"),
        models.LanguageChoice(code="zh-hans", name="Simplified Chinese"),
    ]
    fake_result = models.LanguagePreference(
        supported=fake_supported,
        unsupported=[],
        confirm_label="Confirm",
        cancel_label="Cancel",
        confirm_text="Enable translation for English, Japanese, and Simplified Chinese?",
        cancel_text="Canceled. Please tell me all languages again.",
        completion_text="Enabled translation for your selected languages.",
        primary_language="en",
    )

    line = DummyLineClient()
    repo = DummyRepo()
    handler = MessageHandler(
        line_client=line,
        translation_service=DummyTranslationService(),
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(fake_result),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        translation_retry=1,
        bot_mention_name="bot",
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="reply-token",
        timestamp=0,
        text="English, Japanese, simplified chinese",
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler._attempt_language_enrollment(event)

    template = line.sent["messages"][0]["template"]
    assert template["text"] == "English、Japanese、Simplified Chineseの翻訳を有効にしますか？"
    confirm_payload = _decode_payload(template["actions"][0]["data"])
    assert confirm_payload["primary_language"] == "en"
    assert confirm_payload["completion_text"] == "English、Japanese、Simplified Chineseの翻訳を有効にしました。"
    cancel_payload = _decode_payload(template["actions"][1]["data"])
    assert cancel_payload["cancel_text"] == "Canceled. Please tell me all languages again."


def _decode_payload(data: str):
    import base64
    import json
    import zlib

    assert data.startswith("langpref2=")
    raw = data.split("=", 1)[1]
    padding = "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(raw + padding)
    decompressed = zlib.decompress(decoded)
    return json.loads(decompressed)
