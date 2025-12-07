import sys
import types

from src.app.handlers.message_handler import MessageHandler


class _Dummy:
    """依存関係のダミーオブジェクト。"""


def _fake_stripe_module(session_id: str = "cs_test_short", url: str = "https://checkout.stripe.com/c/pay/mock"):
    module = types.ModuleType("stripe")

    class _SessionApi:
        @staticmethod
        def create(**_kwargs):
            return types.SimpleNamespace(id=session_id, url=url)

    module.checkout = types.SimpleNamespace(Session=_SessionApi)
    return module


def _build_handler(checkout_base: str):
    return MessageHandler(
        line_client=_Dummy(),
        translation_service=_Dummy(),
        interface_translation=_Dummy(),
        language_detector=_Dummy(),
        language_pref_service=_Dummy(),
        command_router=_Dummy(),
        repo=_Dummy(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        stripe_secret_key="sk_test_123",
        stripe_price_monthly_id="price_123",
        free_quota_per_month=50,
        checkout_base_url=checkout_base,
    )


def test_build_checkout_url_uses_short_redirect(monkeypatch):
    monkeypatch.setitem(sys.modules, "stripe", _fake_stripe_module())

    handler = _build_handler("https://api.example.com/stg")
    url = handler._build_checkout_url("group1")

    assert url == "https://api.example.com/stg/checkout?session_id=cs_test_short"


def test_build_checkout_url_falls_back_to_session_url(monkeypatch):
    session_url = "https://checkout.stripe.com/c/pay/cs_test_long#fragment"
    monkeypatch.setitem(sys.modules, "stripe", _fake_stripe_module(url=session_url))

    handler = _build_handler("")
    url = handler._build_checkout_url("group1")

    assert url == session_url
