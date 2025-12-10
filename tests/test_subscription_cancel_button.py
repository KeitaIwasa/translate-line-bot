from src.app.handlers.postback_handler import PostbackHandler
from src.app.handlers.message_handler import MessageHandler
from src.app.subscription_postback import encode_subscription_payload
from src.domain import models
from src.domain.services.interface_translation_service import InterfaceTranslationService


class _Line:
    def __init__(self) -> None:
        self.last_text = None
        self.last_template = None

    def reply_text(self, _token, text):
        self.last_text = text

    def reply_messages(self, _token, messages):
        self.last_template = messages


class _Translator:
    def translate(self, request):
        return [models.TranslationResult(lang=lang, text=f"translated-{lang}") for lang in request.candidate_languages]


class _Dummy:
    """Placeholder dependency; methods are not invoked in these tests."""


class _Repo:
    def fetch_group_languages(self, _group_id):
        return ["ja"]

    def get_subscription_detail(self, _group_id):
        return ("cus", "sub", "canceled")


class _Repo2:
    def fetch_group_languages(self, _group_id):
        return ["ja"]

    def get_subscription_detail(self, _group_id):
        return (None, None, None)


def test_cancel_button_on_canceled_subscription_sends_not_pro_message():
    line = _Line()
    repo = _Repo()
    interface_translation = InterfaceTranslationService(_Translator())

    handler = PostbackHandler(line, repo, interface_translation=interface_translation)

    payload = encode_subscription_payload({
        "kind": "cancel",
        "group_id": "G",
        "instruction_lang": "ja",
    })

    event = models.PostbackEvent(
        event_type="postback",
        reply_token="token",
        timestamp=0,
        data=payload,
        user_id="U",
        group_id="G",
        sender_type="group",
    )

    handler.handle(event)

    assert line.last_text == "translated-ja"
    assert line.last_template is None


def test_mention_cancel_on_non_pro_replies_not_pro_in_primary_language():
    class _Line2:
        def __init__(self):
            self.last_text = None

        def reply_text(self, _token, text):
            self.last_text = text

    line = _Line2()
    repo = _Repo2()
    interface_translation = InterfaceTranslationService(_Translator())

    handler = MessageHandler(
        line_client=line,
        translation_service=_Dummy(),
        interface_translation=interface_translation,
        language_detector=_Dummy(),
        language_pref_service=_Dummy(),
        command_router=_Dummy(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G",
        user_id="U",
        sender_type="group",
        text="cancel",
    )

    # instruction_lang 未設定でもグループ主要言語 (ja) で返信される
    handler._handle_subscription_cancel(event, "")

    assert line.last_text == "translated-ja"
