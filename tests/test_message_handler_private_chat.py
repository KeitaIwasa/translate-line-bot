from unittest.mock import MagicMock

from src.app.handlers.message_handler import MessageHandler
from src.domain import models


class _DummyLineClient:
    def __init__(self):
        self.reply_text_calls = 0
        self.reply_messages_calls = 0

    def reply_text(self, *_args, **_kwargs):
        self.reply_text_calls += 1

    def reply_messages(self, *_args, **_kwargs):
        self.reply_messages_calls += 1

    def get_display_name(self, *_args, **_kwargs):
        return "Alice"


class _DummyRepo:
    def __init__(self):
        self.ensure_calls = 0
        self.inserted = []

    def ensure_group_member(self, *_args, **_kwargs):
        self.ensure_calls += 1

    def insert_message(self, message, *_args, **_kwargs):
        self.inserted.append(message)


class _Dummy:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _build_handler() -> tuple[MessageHandler, _DummyLineClient, _DummyRepo]:
    line = _DummyLineClient()
    repo = _DummyRepo()
    handler = MessageHandler(
        line_client=line,
        translation_service=_Dummy(),
        interface_translation=_Dummy(),
        language_detector=_Dummy(),
        language_pref_service=_Dummy(),
        command_router=_Dummy(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="KOTORI",
    )
    return handler, line, repo


def test_direct_message_is_persisted_without_reply_or_group_flow():
    handler, line, repo = _build_handler()
    handler._process_group_message = MagicMock(return_value=True)

    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="U123",
        user_id="U123",
        sender_type="user",
        text="hello",
        timestamp=1700000000000,
    )

    handler.handle(event)

    assert len(repo.inserted) == 1
    assert repo.ensure_calls == 0
    assert line.reply_text_calls == 0
    assert line.reply_messages_calls == 0
    handler._process_group_message.assert_not_called()


def test_direct_message_is_not_persisted_without_user_id():
    handler, _line, repo = _build_handler()
    handler._process_group_message = MagicMock(return_value=True)

    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="U123",
        user_id=None,
        sender_type="user",
        text="hello",
        timestamp=1700000000000,
    )

    handler.handle(event)

    assert len(repo.inserted) == 0
    assert repo.ensure_calls == 0
    handler._process_group_message.assert_not_called()


def test_direct_message_is_not_persisted_without_reply_token():
    handler, _line, repo = _build_handler()
    handler._process_group_message = MagicMock(return_value=True)

    event = models.MessageEvent(
        event_type="message",
        reply_token=None,
        group_id="U123",
        user_id="U123",
        sender_type="user",
        text="hello",
        timestamp=1700000000000,
    )

    handler.handle(event)

    assert len(repo.inserted) == 0
    assert repo.ensure_calls == 0
    handler._process_group_message.assert_not_called()


def test_group_message_keeps_existing_flow_and_persistence():
    handler, _line, repo = _build_handler()
    handler._process_group_message = MagicMock(return_value=True)

    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G123",
        user_id="U123",
        sender_type="group",
        text="hello group",
        timestamp=1700000000000,
    )

    handler.handle(event)

    assert repo.ensure_calls == 1
    assert len(repo.inserted) == 1
    handler._process_group_message.assert_called_once()

