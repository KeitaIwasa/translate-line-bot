from src.app.handlers.follow_handler import FollowHandler, _normalize_lang
from src.domain import models


class RecordingLineClient:
    def __init__(self):
        self.last_reply_text = None
        self.last_reply_messages = None

    def reply_text(self, reply_token, text):
        self.last_reply_text = (reply_token, text)

    def reply_messages(self, reply_token, messages):
        self.last_reply_messages = (reply_token, messages)

    def get_profile(self, source_type, container_id, user_id):
        return None, None

    def get_display_name(self, *_args, **_kwargs):
        return None


class StubTranslator:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def translate(self, request):
        lang = request.candidate_languages[0]
        text = self.mapping.get(lang)
        if not text:
            return []
        return [models.TranslationResult(lang=lang, text=text)]


def build_event(lang=None, name=None):
    return models.FollowEvent(
        event_type="follow",
        reply_token="token",
        group_id=None,
        user_id="U123",
        sender_type="user",
        timestamp=0,
    ), name, lang


def test_supported_language_with_name():
    line = RecordingLineClient()
    translator = StubTranslator()
    handler = FollowHandler(line, translator)

    event, name, lang = build_event(lang="en", name="Alice")
    line.get_profile = lambda *_args, **_kwargs: (name, lang)

    handler.handle(event)

    assert line.last_reply_text is not None
    _, text = line.last_reply_text
    assert "Alice" in text
    assert "nice to meet you" in text


def test_supported_language_with_region_code():
    line = RecordingLineClient()
    translator = StubTranslator()
    handler = FollowHandler(line, translator)

    event, name, lang = build_event(lang="zh-TW", name="寶")
    line.get_profile = lambda *_args, **_kwargs: (name, lang)

    handler.handle(event)

    _, text = line.last_reply_text
    assert "寶" in text
    assert "你好" in text


def test_unknown_language_translated_by_gemini():
    line = RecordingLineClient()
    translator = StubTranslator(mapping={"es": "¡Hola KOTORI!"})
    handler = FollowHandler(line, translator)

    event, name, lang = build_event(lang="es", name="Carlos")
    line.get_profile = lambda *_args, **_kwargs: (name, lang)

    handler.handle(event)

    _, text = line.last_reply_text
    assert "Hola" in text


def test_unknown_language_translation_failure_fallback_sent():
    line = RecordingLineClient()
    translator = StubTranslator(mapping={})
    handler = FollowHandler(line, translator)

    event, name, lang = build_event(lang="id", name="Andi")
    line.get_profile = lambda *_args, **_kwargs: (name, lang)

    handler.handle(event)

    reply = line.last_reply_messages
    assert reply is not None
    _, messages = reply
    assert len(messages) == 4  # ja, th, zh, en


def test_language_missing_sends_fallback_without_name_artifacts():
    line = RecordingLineClient()
    translator = StubTranslator()
    handler = FollowHandler(line, translator)

    event, name, lang = build_event(lang=None, name=None)
    line.get_profile = lambda *_args, **_kwargs: (name, lang)

    handler.handle(event)

    _, messages = line.last_reply_messages
    assert messages[3]["text"].startswith("Nice to meet you!")


def test_normalize_lang_removes_region():
    assert _normalize_lang("en-US") == "en"
    assert _normalize_lang("zh-Hant") == "zh"
    assert _normalize_lang(None) == ""
