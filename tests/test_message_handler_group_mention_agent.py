import json
import logging
from datetime import datetime, timezone

from src.app.handlers.message_handler import MessageHandler
from src.domain import models


class _Line:
    def __init__(self):
        self.last_text = None
        self.last_messages = None

    def reply_text(self, _token, text):
        self.last_text = text

    def reply_messages(self, *_args, **_kwargs):
        self.last_messages = _args[1] if len(_args) > 1 else _kwargs.get("messages")

    def get_display_name(self, *_args, **_kwargs):
        return None


class _Dummy:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _LangDetector:
    def __init__(self, value="en"):
        self.value = value

    def detect(self, _text):
        return self.value


class _Repo:
    def __init__(self):
        self.translation_enabled_calls = []
        self.runtime = models.TranslationRuntimeState(
            translation_enabled=True,
            group_languages=["ja", "en"],
            subscription_status="active",
            period_start=datetime(2026, 2, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 3, 1, tzinfo=timezone.utc),
            period_key="2026-02-01",
            usage=123,
            limit_notice_plan=None,
            entitlement_plan="standard",
            billing_interval="month",
            is_grandfathered=False,
            quota_anchor_day=1,
            scheduled_target_price_id=None,
            scheduled_effective_at=None,
        )

    def fetch_translation_runtime_state(self, _group_id):
        return self.runtime

    def set_translation_enabled(self, _group_id, enabled):
        self.translation_enabled_calls.append(enabled)

    def fetch_group_languages(self, _group_id):
        return ["ja", "en"]


def _event() -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G1",
        user_id="U1",
        sender_type="group",
        text="@KOTORI test",
        timestamp=1700000000000,
    )


def _build_handler(command_router, repo=None, lang_detector=None):
    return MessageHandler(
        line_client=_Line(),
        translation_service=_Dummy(),
        interface_translation=_Dummy(),
        language_detector=lang_detector or _LangDetector(),
        language_pref_service=_Dummy(),
        command_router=command_router,
        repo=repo or _Repo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="KOTORI",
    )


def test_howto_uses_ack_text_without_forcing_resume():
    class _Router:
        def decide(self, _text):
            return models.CommandDecision(action="howto", instruction_language="ja", ack_text="使い方はこの通りです。")

    repo = _Repo()
    handler = _build_handler(_Router(), repo=repo)
    event = _event()

    handler._handle_command(event, "使い方を教えて")

    assert handler._line.last_text == "使い方はこの通りです。"
    assert repo.translation_enabled_calls == []


def test_pause_uses_single_language_ack_text():
    class _Router:
        def decide(self, _text):
            return models.CommandDecision(action="pause", instruction_language="ja", ack_text="翻訳を停止します。")

    repo = _Repo()
    handler = _build_handler(_Router(), repo=repo)
    event = _event()

    handler._handle_command(event, "停止して")

    assert handler._line.last_text == "翻訳を停止します。"
    assert repo.translation_enabled_calls == [False]


def test_error_replies_and_keeps_state_unchanged():
    class _Router:
        def decide(self, _text):
            return models.CommandDecision(action="error", instruction_language="", ack_text="")

    repo = _Repo()
    handler = _build_handler(_Router(), repo=repo, lang_detector=_LangDetector(value="en"))
    event = _event()

    handler._handle_command(event, "something")

    assert "Sorry, I couldn't process that request right now." in (handler._line.last_text or "")
    assert repo.translation_enabled_calls == []


def test_router_receives_runtime_payload_json():
    class _Router:
        def __init__(self):
            self.payload = None

        def decide(self, text):
            self.payload = text
            return models.CommandDecision(action="howto", instruction_language="en", ack_text="ok")

    repo = _Repo()
    router = _Router()
    handler = _build_handler(router, repo=repo)
    event = _event()

    handler._handle_command(event, "subscription status?")

    assert router.payload is not None
    data = json.loads(router.payload)
    assert data["user_message"] == "subscription status?"
    assert data["subscription_status"] == "active"
    assert data["effective_plan"] == "standard"
    assert data["usage_this_cycle"] == 123
    assert data["next_reset_at_utc"] == "2026-03-01T00:00:00+00:00"
    assert data["current_languages"] == ["ja", "en"]
    assert data["translation_enabled"] is True


def test_command_logs_decision_timing_and_text_reply_metadata_without_body(caplog):
    class _Router:
        def decide(self, _text):
            return models.CommandDecision(
                action="howto",
                instruction_language="ja",
                ack_text="秘密の返信本文はログに出さない",
            )

    caplog.set_level(logging.INFO, logger="src.app.handlers.message_handler")
    handler = _build_handler(_Router())

    handler._handle_command(_event(), "使い方")

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "Command decision | action=howto instruction_lang=ja" in logs
    assert "ack_present=True" in logs
    assert "Command stage | stage=runtime_fetch" in logs
    assert "Command stage | stage=router_decision" in logs
    assert "Command stage | stage=instruction_language_resolution" in logs
    assert "Command stage | stage=action_handler" in logs
    assert "Command reply | status=sent reply_type=text message_count=1" in logs
    assert "Command completed | status=success action=howto" in logs
    assert "total_elapsed_ms=" in logs
    assert "秘密の返信本文はログに出さない" not in logs


def test_command_logs_template_structure_without_labels_or_signed_urls(caplog):
    class _Router:
        def decide(self, _text):
            return models.CommandDecision(action="subscription_menu", instruction_language="en")

    class _SubscriptionRepo(_Repo):
        def get_subscription_period(self, _group_id):
            return ("active", None, None)

        def get_subscription_plan(self, _group_id):
            return ("active", "pro", "month", False, None, None, None, None, None, None)

    class _SubscriptionService:
        def create_checkout_url(self, _group_id):
            return "https://example.test/pro.html?st=secret-token"

    caplog.set_level(logging.INFO, logger="src.app.handlers.message_handler")
    handler = MessageHandler(
        line_client=_Line(),
        translation_service=_Dummy(),
        interface_translation=_Dummy(),
        language_detector=_LangDetector(),
        language_pref_service=_Dummy(),
        command_router=_Router(),
        repo=_SubscriptionRepo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="KOTORI",
        subscription_service=_SubscriptionService(),
    )

    handler._handle_command(_event(), "サブスク確認")

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "Command decision | action=subscription_menu" in logs
    assert "Command reply | status=sent reply_type=template message_count=1" in logs
    assert "template_types=['buttons']" in logs
    assert "action_count=2" in logs
    assert "Manage billing" not in logs
    assert "secret-token" not in logs
