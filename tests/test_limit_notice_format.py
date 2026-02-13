from datetime import datetime, timezone

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


def test_pro_limit_notice_includes_reset_date_without_plan_change_prompt():
    handler = _build_handler()
    handler._build_multilingual_interface_message = lambda base, _gid: base  # type: ignore[assignment]

    notice, url = handler._build_limit_reached_notice_text(
        "group1",
        plan_key="pro",
        limit=8000,
        period_end=datetime(2026, 2, 28, 0, 0, tzinfo=timezone.utc),
    )

    assert url is None
    assert "translation has stopped" in notice
    assert "2026-02-28" in notice
    assert "change your plan" not in notice.lower()


def test_standard_limit_notice_includes_reset_date_and_pro_upgrade_prompt():
    handler = _build_handler()
    handler._build_multilingual_interface_message = lambda base, _gid: base  # type: ignore[assignment]
    handler._subscription_service.create_checkout_url = lambda _gid: "https://short.example.com/cs"  # type: ignore[attr-defined]

    notice, url = handler._build_limit_reached_notice_text(
        "group1",
        plan_key="standard",
        limit=4000,
        period_end=datetime(2026, 3, 15, 0, 0, tzinfo=timezone.utc),
    )

    assert url == "https://short.example.com/cs"
    assert "2026-03-15" in notice
    assert "upgrade to the Pro plan" in notice
