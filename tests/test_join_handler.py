import datetime
from unittest.mock import MagicMock

from src.app.handlers.join_handler import JoinHandler
from src.domain import models


def _make_event(group_id="gid", reply_token="rpt", timestamp_ms=0):
    return models.JoinEvent(
        event_type="join",
        reply_token=reply_token,
        group_id=group_id,
        user_id="uid",
        sender_type="group",
        timestamp=timestamp_ms,
    )


def test_join_handler_saves_group_name_and_calls_repo():
    line = MagicMock()
    line.get_group_name.return_value = "Sample Group"
    repo = MagicMock()

    handler = JoinHandler(line, repo)
    event = _make_event(timestamp_ms=int(datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000))

    handler.handle(event)

    repo.record_bot_joined_at.assert_called_once()
    repo.upsert_group_name.assert_called_once_with("gid", "Sample Group")
    repo.reset_group_language_settings.assert_called_once_with("gid")
    repo.set_translation_enabled.assert_called_once_with("gid", False)
    line.reply_text.assert_called_once()


def test_join_handler_continues_when_group_name_missing():
    line = MagicMock()
    line.get_group_name.return_value = None
    repo = MagicMock()

    handler = JoinHandler(line, repo)
    event = _make_event()

    handler.handle(event)

    repo.record_bot_joined_at.assert_called_once()
    repo.upsert_group_name.assert_not_called()
    line.reply_text.assert_called_once()


def test_join_handler_swallows_group_name_errors():
    line = MagicMock()
    line.get_group_name.side_effect = RuntimeError("boom")
    repo = MagicMock()

    handler = JoinHandler(line, repo)
    event = _make_event()

    handler.handle(event)

    repo.record_bot_joined_at.assert_called_once()
    line.reply_text.assert_called_once()
