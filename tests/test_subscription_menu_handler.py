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


def test_subscription_upgrade_returns_same_menu_as_subscription_menu():
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
        text="upgrade",
    )

    assert handler._handle_subscription_upgrade(event, "en") is True
    assert subscription_service.portal_calls == []
    assert subscription_service.checkout_calls == ["G", "G"]
    assert line.messages is not None
    assert line.messages[0]["template"]["actions"][0]["label"] == "Manage billing"
    assert line.messages[0]["template"]["actions"][0]["uri"] == "https://frontend.example.com/pro.html?st=token"


class _FreeRepo:
    def get_subscription_period(self, _group_id):
        return (None, None, None)

    def get_subscription_plan(self, _group_id):
        return (None, "free", "month", False, None, None, None, None, None, None)


class _BatchInterface:
    def __init__(self, translated=None, exc=None):
        self.calls = []
        self.translated = translated
        self.exc = exc

    def translate_many(self, texts, lang):
        copied = list(texts)
        self.calls.append((copied, lang))
        if self.exc:
            raise self.exc
        if self.translated is not None:
            return self.translated
        return [f"{lang}:{text}" for text in copied]


def _free_menu_handler(line, interface):
    return MessageHandler(
        line_client=line,
        translation_service=_Dummy(),
        interface_translation=interface,
        language_detector=_Dummy(),
        language_pref_service=_Dummy(),
        command_router=_Dummy(),
        repo=_FreeRepo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        subscription_service=_SubscriptionService(),
    )


def _menu_event():
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G",
        user_id="U",
        sender_type="group",
        text="upgrade",
    )


def test_free_plan_menu_batches_five_strings_once():
    line = _Line()
    interface = _BatchInterface()
    handler = _free_menu_handler(line, interface)

    assert handler._handle_subscription_menu(_menu_event(), "ja") is True

    assert len(interface.calls) == 1
    texts, lang = interface.calls[0]
    assert lang == "ja"
    assert len(texts) == 5
    assert texts[1:] == [
        "Subscription",
        "Manage your plan. Choose an action below.",
        "Manage billing",
        "Upgrade to Pro",
    ]
    labels = [action["label"] for action in line.messages[0]["template"]["actions"]]
    assert labels == ["ja:Manage billing", "ja:Upgrade to Pro"]
    assert line.messages[0]["template"]["title"] == "ja:Subscription"


def test_free_plan_menu_keeps_english_when_batch_translation_fails():
    line = _Line()
    interface = _BatchInterface(exc=RuntimeError("parse failed"))
    handler = _free_menu_handler(line, interface)

    assert handler._handle_subscription_menu(_menu_event(), "ja") is True

    assert len(interface.calls) == 1
    labels = [action["label"] for action in line.messages[0]["template"]["actions"]]
    assert labels == ["Manage billing", "Upgrade to Pro"]
    assert line.messages[0]["template"]["title"] == "Subscription"
