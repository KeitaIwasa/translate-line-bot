import json
from datetime import datetime, timezone

from src.app.handlers.message_handler import MessageHandler
from src.domain import models


class _Line:
    def __init__(self):
        self.last_text = None

    def reply_text(self, _token, text):
        self.last_text = text

    def reply_messages(self, *_args, **_kwargs):
        return None

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
