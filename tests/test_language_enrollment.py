import pytest

from src.app.handlers.message_handler import MessageHandler
from src.presentation.rich_message import visible_reply_text
from src.domain import models
from src.domain.services.translation_service import TranslationService
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.language_detection_service import LanguageDetectionService
from src.app.handlers.postback_handler import PostbackHandler


@pytest.fixture(autouse=True)
def _isolate_required_runtime_env(monkeypatch):
    """収集時にプロセス全体へダミーキーを残さない。ライブ Gemini テストが dummy を本物と誤認する。"""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "dummy")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://dummy")


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

    def increment_usage(self, *args, **kwargs):
        return 0

    def get_usage(self, *args, **kwargs):
        return 0

    def get_subscription_status(self, *args, **kwargs):
        return "active"

    def upsert_subscription(self, *args, **kwargs):
        return None

    def update_subscription_status(self, *args, **kwargs):
        return None


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
        max_group_languages=5,
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
    assert template["text"] == "Do you want to enable translation for 日本語 and アラビア語?"

    confirm_payload = _decode_payload(template["actions"][0]["data"])
    langs = [item["code"] for item in confirm_payload["languages"]]
    assert langs == ["ja", "ar"]
    assert (
        confirm_payload["completion_text"]
        == "日本語 and アラビア語 have been set as the translation languages."
    )
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
        max_group_languages=5,
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
    assert template["text"] == "Do you want to enable translation for English, Japanese, and Simplified Chinese?"
    confirm_payload = _decode_payload(template["actions"][0]["data"])
    assert confirm_payload["primary_language"] == "en"
    assert (
        confirm_payload["completion_text"]
        == "English, Japanese, and Simplified Chinese have been set as the translation languages."
    )
    cancel_payload = _decode_payload(template["actions"][1]["data"])
    assert (
        cancel_payload["cancel_text"]
        == "Language update has been cancelled. Please tell me all languages again."
    )


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


def test_language_enrollment_rejects_over_five():
    fake_supported = [
        models.LanguageChoice(code="en", name="English"),
        models.LanguageChoice(code="ja", name="Japanese"),
        models.LanguageChoice(code="fr", name="French"),
        models.LanguageChoice(code="de", name="German"),
        models.LanguageChoice(code="th", name="Thai"),
        models.LanguageChoice(code="es", name="Spanish"),
    ]
    fake_result = models.LanguagePreference(
        supported=fake_supported,
        unsupported=[],
        confirm_label="OK",
        cancel_label="Cancel",
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
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="reply-token",
        timestamp=0,
        text="lots of languages",
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler._attempt_language_enrollment(event)

    messages = line.sent["messages"]
    assert len(messages) == 1
    assert messages[0]["type"] == "text"
    assert "最大5つまでだよ" in messages[0]["text"]
    assert "English" in messages[0]["text"]


def test_language_enrollment_rejects_when_total_detected_exceeds_even_with_unsupported():
    fake_supported = [
        models.LanguageChoice(code="ja", name="Japanese"),
        models.LanguageChoice(code="ru", name="Russian"),
        models.LanguageChoice(code="zh-hans", name="Simplified Chinese"),
        models.LanguageChoice(code="th", name="Thai"),
    ]
    fake_unsupported = [
        models.LanguageChoice(code="en", name="English"),
        models.LanguageChoice(code="zh-hant", name="Traditional Chinese"),
    ]

    fake_result = models.LanguagePreference(
        supported=fake_supported,
        unsupported=fake_unsupported,
        confirm_label="OK",
        cancel_label="Cancel",
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
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="reply-token",
        timestamp=0,
        text="ja ru zh-hans th en zh-hant",
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler._attempt_language_enrollment(event)

    messages = line.sent["messages"]
    assert messages[0]["type"] == "text"
    assert "Traditional Chinese" in messages[0]["text"]
    template = messages[-1]["template"]
    assert template["type"] == "confirm"
    langs = [item["code"] for item in _decode_payload(template["actions"][0]["data"])["languages"]]
    assert langs == ["ja", "ru", "zh-hans", "th"]


class RecordingRepo(DummyRepo):
    def __init__(self, initial):
        super().__init__()
        self.languages = list(initial)
        self.translation_enabled = True
        self.replace_called = False
        self.apply_called = False
        self.reset_called = False

    def fetch_group_languages(self, *_args, **_kwargs):
        return list(self.languages)

    def add_group_languages(self, _group_id, languages):
        for code, _name in languages:
            if code not in self.languages:
                self.languages.append(code)

    def remove_group_languages(self, _group_id, lang_codes):
        lowered = {code.lower() for code in lang_codes}
        self.languages = [lang for lang in self.languages if lang.lower() not in lowered]

    def set_translation_enabled(self, _group_id, enabled: bool):
        self.translation_enabled = enabled

    def replace_group_languages(self, _group_id, languages):
        self.replace_called = True
        self.languages = [code for code, _name in languages if code]
        self.translation_enabled = True

    def apply_language_changes(self, _group_id, languages_to_add, languages_to_remove):
        self.apply_called = True
        lowered = {(code or "").lower() for code in languages_to_remove if code}
        self.languages = [lang for lang in self.languages if lang.lower() not in lowered]
        for code, _name in languages_to_add:
            if code and code not in self.languages:
                self.languages.append(code)


class RecordingLineClient(DummyLineClient):
    def __init__(self):
        super().__init__()
        self.last_text = None

    def reply_text(self, reply_token, text):
        self.last_text = text
        super().reply_text(reply_token, text)

    def reply_messages(self, reply_token, messages):
        self.last_text = visible_reply_text(messages)
        super().reply_messages(reply_token, messages)


def test_language_settings_add_rejects_when_exceeding_limit():
    line = RecordingLineClient()
    repo = RecordingRepo(initial=["en", "ja", "fr", "de", "th"])
    handler = MessageHandler(
        line_client=line,
        translation_service=DummyTranslationService(),
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(models.LanguagePreference(supported=[])),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )

    decision = models.CommandDecision(
        action="language_settings",
        operation="add",
        languages_to_add=[
            models.LanguageChoice(code="es", name="Spanish"),
            models.LanguageChoice(code="it", name="Italian"),
        ],
        instruction_language="ja",
        ack_text="",
    )
    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text="@bot add languages",
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler._handle_language_settings(event, decision, event.text)

    assert repo.languages == ["en", "ja", "fr", "de", "th"]
    assert "最大5つまで" in (line.last_text or "")
    assert "どれを外す" in (line.last_text or "")
    assert "英語" in (line.last_text or "")


def test_language_settings_ack_without_static_text_uses_translation_path():
    class RecordingTranslation(DummyTranslationService):
        def __init__(self):
            self.texts = []

        def translate(
            self,
            sender_name,
            message_text,
            timestamp,
            context_messages,
            candidate_languages,
            allow_same_language=False,
        ):
            self.texts.append((message_text, list(candidate_languages)))
            return [models.TranslationResult(lang=candidate_languages[0], text="Paramètres de langue mis à jour.")]

    line = RecordingLineClient()
    repo = RecordingRepo(initial=["ja"])
    translation = RecordingTranslation()
    handler = MessageHandler(
        line_client=line,
        translation_service=translation,
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(models.LanguagePreference(supported=[])),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )
    decision = models.CommandDecision(
        action="language_settings",
        operation="add",
        languages_to_add=[models.LanguageChoice(code="fr", name="French")],
        instruction_language="fr",
        ack_text="",
    )
    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text="@bot ajoute le français",
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    assert handler._handle_language_settings(event, decision, event.text) is True
    assert translation.texts
    assert translation.texts[0][1] == ["fr"]
    assert "Japanese and French have been set as the translation languages." in translation.texts[0][0]
    assert line.last_text == "Paramètres de langue mis à jour."
    assert "fr" in repo.languages


def test_spanish_instruction_translates_selection_limit_and_unsupported_replies():
    """固定文が無い言語は、英語の原文を指示言語へ翻訳する。"""

    class RecordingTranslation(DummyTranslationService):
        def __init__(self):
            self.texts = []

        def translate(
            self,
            sender_name,
            message_text,
            timestamp,
            context_messages,
            candidate_languages,
            allow_same_language=False,
        ):
            self.texts.append((message_text, list(candidate_languages)))
            return [models.TranslationResult(lang=candidate_languages[0], text=f"ES:{message_text}")]

    line = RecordingLineClient()
    repo = RecordingRepo(initial=["en", "ja", "ko", "th", "fr"])
    translation = RecordingTranslation()
    handler = MessageHandler(
        line_client=line,
        translation_service=translation,
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(models.LanguagePreference(supported=[])),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )

    selection = models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        instruction_language="es",
        explicit_reset=True,
    )
    assert handler._handle_language_settings(_settings_event("all languages"), selection, "all languages") is True
    assert repo.reset_called is False
    assert translation.texts[-1][1] == ["es"]
    assert "Which ones should I use?" in translation.texts[-1][0]
    assert line.last_text.startswith("ES:")

    limit = models.CommandDecision(
        action="language_settings",
        operation="add",
        languages_to_add=[models.LanguageChoice(code="vi", name="Vietnamese")],
        instruction_language="es",
    )
    assert handler._handle_language_settings(_settings_event("add Vietnamese"), limit, "add Vietnamese") is True
    assert repo.languages == ["en", "ja", "ko", "th", "fr"]
    assert "swap out" in translation.texts[-1][0]
    assert translation.texts[-1][1] == ["es"]
    assert line.last_text.startswith("ES:")

    unsupported = models.CommandDecision(
        action="language_settings",
        operation="add",
        instruction_language="es",
    )
    assert handler._handle_language_settings(_settings_event("エルヴ語"), unsupported, "エルヴ語") is True
    assert "isn't supported" in translation.texts[-1][0]
    assert translation.texts[-1][1] == ["es"]
    assert line.last_text.startswith("ES:")


def test_english_instruction_keeps_the_fixed_reply_without_translation():
    class RecordingTranslation(DummyTranslationService):
        def translate(self, *args, **kwargs):
            raise AssertionError("English fixed text should not be translated")

    line = RecordingLineClient()
    repo = RecordingRepo(initial=["ja"])
    handler = MessageHandler(
        line_client=line,
        translation_service=RecordingTranslation(),
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(models.LanguagePreference(supported=[])),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )
    decision = models.CommandDecision(
        action="language_settings",
        operation="add",
        languages_to_add=[models.LanguageChoice(code="fr", name="French")],
        instruction_language="en",
    )
    assert handler._handle_language_settings(_settings_event("add French"), decision, "add French") is True
    assert line.last_text == "Japanese and French have been set as the translation languages."


def _settings_event(text: str) -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text=text,
        user_id="U",
        group_id="G",
        sender_type="group",
    )


def _settings_handler(repo, line):
    return MessageHandler(
        line_client=line,
        translation_service=DummyTranslationService(),
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(models.LanguagePreference(supported=[])),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )


class _ClearingRepo(RecordingRepo):
    def reset_group_language_settings(self, *_args, **_kwargs):
        self.languages = []
        self.reset_called = True


def test_reset_all_with_add_list_replaces_languages_and_keeps_translation():
    """Gemini が reset_all と英語の追加を返しても、全消去して翻訳停止にはしない。"""

    line = RecordingLineClient()
    repo = _ClearingRepo(initial=["ja", "th"])
    repo.translation_enabled = False
    repo.reset_called = False
    handler = _settings_handler(repo, line)
    decision = models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        languages_to_add=[models.LanguageChoice(code="en", name="English")],
        languages_to_remove=[models.LanguageChoice(code="ja", name="Japanese")],
        instruction_language="en",
        ack_text="Your language settings have been reset. Please tell us all the languages you would like to translate.",
        explicit_reset=True,
    )

    assert handler._handle_language_settings(
        _settings_event("@bot 英語だけにして"),
        decision,
        "英語だけにして",
    ) is True
    assert repo.reset_called is False
    assert repo.replace_called is True
    assert repo.languages == ["en"]
    assert repo.translation_enabled is True
    assert line.last_text == "English has been set as the translation language."
    assert "reset" not in (line.last_text or "").lower()


def test_reset_all_with_several_languages_sets_exactly_that_list():
    """置き換え後の言語は追加リストと一致し、確認文は新しい言語を並べる。"""

    line = RecordingLineClient()
    repo = _ClearingRepo(initial=["th", "fr", "ja"])
    handler = _settings_handler(repo, line)
    decision = models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        languages_to_add=[
            models.LanguageChoice(code="en", name="English"),
            models.LanguageChoice(code="ja", name="Japanese"),
        ],
        instruction_language="en",
        ack_text="リセットしました",
        explicit_reset=True,
    )

    assert handler._handle_language_settings(_settings_event("@bot 英語と日本語だけにして"), decision, "英語と日本語だけにして") is True
    assert repo.languages == ["en", "ja"]
    assert repo.translation_enabled is True
    assert line.last_text == "English and Japanese have been set as the translation languages."
    assert "reset" not in (line.last_text or "").lower()


def test_replace_over_the_language_limit_does_not_clear_current_languages():
    line = RecordingLineClient()
    repo = _ClearingRepo(initial=["ja", "en"])
    repo.reset_called = False
    handler = _settings_handler(repo, line)
    decision = models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        languages_to_add=[
            models.LanguageChoice(code=code, name=code.upper())
            for code in ["en", "ja", "fr", "de", "th", "es"]
        ],
        instruction_language="en",
        explicit_reset=True,
    )

    assert handler._handle_language_settings(
        _settings_event("@bot 言語を6つにして"),
        decision,
        "言語を6つにして",
    ) is True
    assert repo.reset_called is False
    assert repo.languages == ["ja", "en"]
    assert "You can set up to 5 translation languages" in (line.last_text or "")


def test_plain_reset_all_still_clears_languages_and_stops_translation():
    line = RecordingLineClient()
    repo = _ClearingRepo(initial=["ja", "en"])
    handler = _settings_handler(repo, line)
    decision = models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        instruction_language="en",
        ack_text="リセットしました",
        explicit_reset=True,
    )

    assert handler._handle_language_settings(_settings_event("@bot 言語設定をリセットして"), decision, "言語設定をリセットして") is True
    assert repo.languages == []
    assert repo.translation_enabled is False
    assert "have been reset" in (line.last_text or "")


def test_language_settings_static_ack_is_not_translated_again():
    class RecordingTranslation(DummyTranslationService):
        def translate(self, *args, **kwargs):
            raise AssertionError("static ack should be used as-is")

    line = RecordingLineClient()
    repo = RecordingRepo(initial=["en"])
    handler = MessageHandler(
        line_client=line,
        translation_service=RecordingTranslation(),
        interface_translation=DummyInterfaceTranslation(),
        language_detector=LanguageDetectionService(),
        language_pref_service=DummyLangPrefService(models.LanguagePreference(supported=[])),
        command_router=DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )
    decision = models.CommandDecision(
        action="language_settings",
        operation="add",
        languages_to_add=[models.LanguageChoice(code="ja", name="Japanese")],
        instruction_language="ja",
        ack_text="言語設定を更新しました。",
    )
    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text="@bot 日本語を追加",
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    assert handler._handle_language_settings(event, decision, event.text) is True
    assert line.last_text == "英語・日本語になったよ"


def test_postback_rejects_over_limit():
    class _Line:
        def __init__(self):
            self.last = None

        def reply_text(self, _token, text):
            self.last = text

    class _Repo(DummyRepo):
        def __init__(self):
            super().__init__()
            self.saved = False

        def try_complete_group_languages(self, *_args, **_kwargs):
            self.saved = True
            return True

    line = _Line()
    repo = _Repo()
    handler = PostbackHandler(line, repo, max_group_languages=5)

    payload = {
        "kind": "language_confirm",
        "action": "confirm",
        "languages": [
            {"code": c, "name": c.upper()}
            for c in ["en", "ja", "fr", "de", "th", "es"]
        ],
        "primary_language": "ja",
    }
    data = MessageHandler._encode_postback_payload(payload)  # type: ignore[attr-defined]

    event = models.PostbackEvent(
        event_type="postback",
        reply_token="token",
        timestamp=0,
        data=data,
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler.handle(event)

    assert repo.saved is False
    assert line.last and "最大5つまでだよ" in line.last


def test_postback_sends_multilingual_completion():
    class _Line:
        def __init__(self):
            self.last = None

        def reply_text(self, _token, text):
            self.last = text

    class _Translator:
        def translate(self, request):
            return [
                models.TranslationResult(lang=lang, text=f"{request.message_text} ({lang})")
                for lang in request.candidate_languages
            ]

    class _Repo(DummyRepo):
        def __init__(self):
            super().__init__()
            self.saved = False

        def try_complete_group_languages(self, *_args, **_kwargs):
            self.saved = True
            return True

    line = _Line()
    repo = _Repo()
    interface_translation = InterfaceTranslationService(_Translator())
    handler = PostbackHandler(
        line,
        repo,
        max_group_languages=5,
        interface_translation=interface_translation,
    )

    payload = {
        "kind": "language_confirm",
        "action": "confirm",
        "languages": [
            {"code": "en", "name": "English"},
            {"code": "ja", "name": "Japanese"},
            {"code": "th", "name": "Thai"},
        ],
        "primary_language": "en",
    }
    data = MessageHandler._encode_postback_payload(payload)  # type: ignore[attr-defined]

    event = models.PostbackEvent(
        event_type="postback",
        reply_token="token",
        timestamp=0,
        data=data,
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler.handle(event)

    assert repo.saved is True
    assert line.last is not None
    parts = line.last.split("\n\n")
    assert parts[0] == "English, Japanese, and Thai have been set as the translation languages."
    assert "(ja)" in parts[1]
    assert "(th)" in parts[2]
