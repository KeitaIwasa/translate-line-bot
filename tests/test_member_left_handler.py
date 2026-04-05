from datetime import datetime, timezone

from src.app.handlers.member_left_handler import MemberLeftHandler
from src.domain import models


class _Line:
    def __init__(self) -> None:
        self.messages = []

    def push_text(self, to, text):
        self.messages.append((to, text))


class _Repo:
    def __init__(self, owner_id="OWNER") -> None:
        self.owner_id = owner_id
        self.left = []

    def get_billing_owner_user_id(self, _group_id):
        return self.owner_id

    def mark_group_member_left(self, group_id, user_id, left_at=None):
        self.left.append((group_id, user_id, left_at))


class _SubscriptionService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def reserve_cancellation_on_owner_leave(self, group_id):
        self.calls.append(group_id)
        return self.result


def test_member_left_ignores_non_owner_leave():
    line = _Line()
    repo = _Repo(owner_id="OWNER")
    service = _SubscriptionService(result=None)
    handler = MemberLeftHandler(line, repo, service)

    event = models.MemberLeftEvent(
        event_type="memberLeft",
        reply_token=None,
        group_id="G",
        user_id="U0",
        sender_type="group",
        left_user_ids=["U1"],
        timestamp=0,
    )
    handler.handle(event)

    assert repo.left == [("G", "U1", None)]
    assert service.calls == []
    assert line.messages == []


def test_member_left_reserves_cancellation_and_pushes_notice():
    line = _Line()
    repo = _Repo(owner_id="OWNER")
    service = _SubscriptionService(
        result={"current_period_end": datetime(2026, 4, 30, tzinfo=timezone.utc)}
    )
    handler = MemberLeftHandler(line, repo, service)

    event = models.MemberLeftEvent(
        event_type="memberLeft",
        reply_token=None,
        group_id="G",
        user_id="U0",
        sender_type="group",
        left_user_ids=["OWNER"],
        timestamp=0,
    )
    handler.handle(event)

    assert service.calls == ["G"]
    assert line.messages and line.messages[0][0] == "G"
    assert "2026-04-30" in line.messages[0][1]
