from src.app.handlers.message_handler import MessageHandler
from src.domain import models


class _Line:
    def __init__(self) -> None:
        self.messages = None

    def reply_text(self, _token, _text):
        raise AssertionError("reply_text should not be called")

    def reply_messages(self, _token, messages):
        self.messages = messages


class _Dummy:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Repo:
    def get_subscription_period(self, _group_id):
        return ("active", None, None)

    def get_subscription_plan(self, _group_id):
        return ("active", "pro", "month", False, None, None, None, None, None, None)


class _SubscriptionService:
    def __init__(self) -> None:
        self.checkout_calls = []
        self.portal_calls = []

    def create_checkout_url(self, group_id):
        self.checkout_calls.append(group_id)
        return "https://frontend.example.com/pro.html?st=token"

    def create_portal_url(self, group_id):
        self.portal_calls.append(group_id)
        return "https://billing.stripe.com/session/should-not-be-used"


def test_subscription_menu_uses_checkout_url_for_manage_billing():
    line = _Line()
    subscription_service = _SubscriptionService()
    handler = MessageHandler(
        line_client=line,
        translation_service=_Dummy(),
        interface_translation=_Dummy(),
        language_detector=_Dummy(),
        language_pref_service=_Dummy(),
        command_router=_Dummy(),
        repo=_Repo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        subscription_service=subscription_service,
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G",
        user_id="U",
        sender_type="group",
        text="subscription",
    )

    assert handler._handle_subscription_menu(event, "en") is True
    assert subscription_service.portal_calls == []
    assert subscription_service.checkout_calls == ["G", "G"]
    assert line.messages is not None
    assert line.messages[0]["template"]["actions"][0]["label"] == "Manage billing"
    assert line.messages[0]["template"]["actions"][0]["uri"] == "https://frontend.example.com/pro.html?st=token"
