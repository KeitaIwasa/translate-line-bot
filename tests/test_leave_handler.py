from unittest.mock import MagicMock

from src.app.handlers.leave_handler import LeaveHandler
from src.domain import models


def _make_event(group_id="gid"):
    return models.LeaveEvent(
        event_type="leave",
        reply_token=None,
        group_id=group_id,
        user_id="uid",
        sender_type="group",
        timestamp=0,
    )


def test_leave_handler_cancels_subscription():
    subscription_service = MagicMock()
    repo = MagicMock()
    handler = LeaveHandler(subscription_service, repo)

    handler.handle(_make_event())

    subscription_service.cancel_subscription.assert_called_once_with("gid")


def test_leave_handler_ignores_event_without_group():
    subscription_service = MagicMock()
    repo = MagicMock()
    handler = LeaveHandler(subscription_service, repo)

    handler.handle(_make_event(group_id=None))

    subscription_service.cancel_subscription.assert_not_called()


def test_leave_handler_retries_up_to_three_times():
    subscription_service = MagicMock()
    subscription_service.cancel_subscription.side_effect = [False, False, False]
    handler = LeaveHandler(subscription_service, MagicMock())

    handler.handle(_make_event())

    assert subscription_service.cancel_subscription.call_count == 3


def test_leave_handler_stops_after_successful_retry():
    subscription_service = MagicMock()
    subscription_service.cancel_subscription.side_effect = [False, True, True]
    handler = LeaveHandler(subscription_service, MagicMock())

    handler.handle(_make_event())

    assert subscription_service.cancel_subscription.call_count == 2
