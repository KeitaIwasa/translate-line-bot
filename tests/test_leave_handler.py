import logging
from unittest.mock import MagicMock

import pytest

from src.app.handlers.leave_handler import LeaveHandler
from src.domain import models


def _repo(*, subscription=("cus", "sub", "active")):
    repo = MagicMock()
    repo.get_subscription_detail.return_value = subscription
    return repo


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
    repo = _repo()
    handler = LeaveHandler(subscription_service, repo)

    handler.handle(_make_event())

    subscription_service.cancel_subscription.assert_called_once_with("gid")
    repo.reset_limit_notice_plan.assert_called_once_with("gid")


def test_leave_handler_ignores_event_without_group():
    subscription_service = MagicMock()
    repo = MagicMock()
    handler = LeaveHandler(subscription_service, repo)

    handler.handle(_make_event(group_id=None))

    subscription_service.cancel_subscription.assert_not_called()


def test_leave_handler_retries_up_to_three_times():
    subscription_service = MagicMock()
    subscription_service.cancel_subscription.side_effect = [False, False, False]
    handler = LeaveHandler(subscription_service, _repo())

    handler.handle(_make_event())

    assert subscription_service.cancel_subscription.call_count == 3


def test_leave_handler_stops_after_successful_retry():
    subscription_service = MagicMock()
    subscription_service.cancel_subscription.side_effect = [False, True, True]
    handler = LeaveHandler(subscription_service, _repo())

    handler.handle(_make_event())

    assert subscription_service.cancel_subscription.call_count == 2


def test_leave_handler_continues_when_reset_fails():
    subscription_service = MagicMock()
    repo = _repo()
    repo.reset_limit_notice_plan.side_effect = RuntimeError("db down")
    handler = LeaveHandler(subscription_service, repo)

    handler.handle(_make_event())

    subscription_service.cancel_subscription.assert_called_once_with("gid")


@pytest.mark.parametrize(
    "detail",
    [(None, None, None), ("cus", None, "free"), ("cus", "", None)],
)
def test_leave_handler_skips_cancel_when_group_has_no_subscription(detail, caplog):
    subscription_service = MagicMock()
    repo = _repo(subscription=detail)
    handler = LeaveHandler(subscription_service, repo)

    with caplog.at_level(logging.INFO):
        handler.handle(_make_event())

    subscription_service.cancel_subscription.assert_not_called()
    repo.reset_limit_notice_plan.assert_called_once_with("gid")
    assert "Skip auto cancel on leave; no subscription" in caplog.text
    assert "Auto cancel on leave failed" not in caplog.text
    assert "gave up after retries" not in caplog.text


def test_leave_handler_still_retries_when_subscription_exists(caplog):
    subscription_service = MagicMock()
    subscription_service.cancel_subscription.return_value = False
    handler = LeaveHandler(subscription_service, _repo(subscription=("cus", "sub_active", "active")))

    with caplog.at_level(logging.WARNING):
        handler.handle(_make_event())

    assert subscription_service.cancel_subscription.call_count == 3
    assert "gave up after retries" in caplog.text


def test_leave_handler_tries_cancel_when_subscription_lookup_fails():
    subscription_service = MagicMock()
    subscription_service.cancel_subscription.return_value = True
    repo = _repo()
    repo.get_subscription_detail.side_effect = RuntimeError("db down")
    handler = LeaveHandler(subscription_service, repo)

    handler.handle(_make_event())

    subscription_service.cancel_subscription.assert_called_once_with("gid")
