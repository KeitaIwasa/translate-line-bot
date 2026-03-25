from src.app.handlers.message_handler import MessageHandler
from src.app.handlers.postback_handler import PostbackHandler
from src.app.subscription_postback import encode_subscription_payload
from src.domain import models


class _Line:
    def __init__(self) -> None:
        self.last_text = None
        self.last_template = None

    def reply_text(self, _token, text):
        self.last_text = text

    def reply_messages(self, _token, messages):
        self.last_template = messages


class _Dummy:
    pass


class _OwnerRepo:
    def fetch_group_languages(self, _group_id):
        return ["ja"]

    def get_subscription_detail(self, _group_id):
        return ("cus", "sub", "active")

    def get_billing_owner_user_id(self, _group_id):
        return "OWNER"


def test_message_cancel_rejects_non_owner():
    line = _Line()
    repo = _OwnerRepo()

    handler = MessageHandler(
        line_client=line,
        translation_service=_Dummy(),
        interface_translation=None,
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

    handler._handle_subscription_cancel(event, "")
    assert line.last_text == "Only the billing owner can manage this subscription."


def test_postback_cancel_rejects_non_owner():
    line = _Line()
    repo = _OwnerRepo()
    handler = PostbackHandler(line, repo, interface_translation=None)

    payload = encode_subscription_payload(
        {
            "kind": "cancel",
            "group_id": "G",
            "instruction_lang": "ja",
        }
    )
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
    assert line.last_text == "Only the billing owner can manage this subscription."
    assert line.last_template is None
