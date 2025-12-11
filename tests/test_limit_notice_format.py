from src.app.handlers.message_handler import MessageHandler


class _RepoDummy:
    """依存関係のダミーオブジェクト。"""

    def fetch_group_languages(self, _group_id):
        return ["en"]


def _build_handler():
    return MessageHandler(
        line_client=_RepoDummy(),
        translation_service=_RepoDummy(),
        interface_translation=_RepoDummy(),
        language_detector=_RepoDummy(),
        language_pref_service=_RepoDummy(),
        command_router=_RepoDummy(),
        repo=_RepoDummy(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        stripe_secret_key="sk",
        stripe_price_monthly_id="price",
        subscription_frontend_base_url="https://frontend.example.com",
        checkout_api_base_url="https://api.example.com",
    )


def test_limit_notice_splits_url():
    handler = _build_handler()
    handler._build_multilingual_interface_message = lambda base, _gid: base  # type: ignore[assignment]
    handler._subscription_service.create_checkout_url = lambda _gid: "https://short.example.com/cs"  # type: ignore[attr-defined]

    notice, url = handler._build_limit_reached_notice_text("group1", paid=False, limit=50)

    assert url == "https://short.example.com/cs"
    assert "https://short.example.com/cs" not in notice


def test_limit_notice_paid_has_no_url():
    handler = _build_handler()
    handler._build_multilingual_interface_message = lambda base, _gid: base  # type: ignore[assignment]
    notice, url = handler._build_limit_reached_notice_text("group1", paid=True, limit=8000)

    assert url is None
    assert "http" not in notice.lower()
