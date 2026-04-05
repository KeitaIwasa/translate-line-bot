from datetime import datetime, timezone

from src.app.handlers.member_left_handler import MemberLeftHandler
from src.domain import models
from src.domain.services.interface_translation_service import InterfaceTranslationService


class _Line:
    def __init__(self, *, fail_targets=None) -> None:
        self.messages = []
        self.fail_targets = set(fail_targets or [])

    def push_text(self, to, text):
        if to in self.fail_targets:
            raise RuntimeError("push failed")
        self.messages.append((to, text))


class _Repo:
    def __init__(self, owner_id="OWNER", languages=None) -> None:
        self.owner_id = owner_id
        self.languages = list(languages or [])
        self.left = []

    def get_billing_owner_user_id(self, _group_id):
        return self.owner_id

    def mark_group_member_left(self, group_id, user_id, left_at=None):
        self.left.append((group_id, user_id, left_at))

    def fetch_group_languages(self, _group_id):
        return list(self.languages)


class _SubscriptionService:
    def __init__(self, result, checkout_url=None):
        self.result = result
        self.checkout_url = checkout_url
        self.calls = []
        self.checkout_calls = []

    def reserve_cancellation_on_owner_leave(self, group_id):
        self.calls.append(group_id)
        return self.result

    def create_checkout_url(self, group_id):
        self.checkout_calls.append(group_id)
        return self.checkout_url


class _Translator:
    def translate(self, request):
        return [models.TranslationResult(lang=lang, text=f"{lang}:{request.message_text}") for lang in request.candidate_languages]


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
        result={"current_period_end": datetime(2026, 4, 30, tzinfo=timezone.utc)},
        checkout_url="https://billing.example.com/manage",
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
    assert [target for target, _ in line.messages] == ["G", "OWNER"]
    assert "2026-04-30" in line.messages[0][1]
    assert "2026-04-30" in line.messages[1][1]
    assert line.messages[0][1].endswith("https://billing.example.com/manage")
    assert service.checkout_calls == ["G"]


def test_member_left_dm_failure_does_not_break_flow():
    line = _Line(fail_targets={"OWNER"})
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
    assert [target for target, _ in line.messages] == ["G"]


def test_member_left_without_checkout_url_keeps_group_message_text_only():
    line = _Line()
    repo = _Repo(owner_id="OWNER")
    service = _SubscriptionService(
        result={"current_period_end": datetime(2026, 4, 30, tzinfo=timezone.utc)},
        checkout_url=None,
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

    assert len(line.messages) == 2
    assert "http" not in line.messages[0][1]


def test_member_left_builds_multilingual_messages_for_group_and_dm():
    line = _Line()
    repo = _Repo(owner_id="OWNER", languages=["en", "ja", "ja", "fr"])
    service = _SubscriptionService(
        result={"current_period_end": datetime(2026, 4, 30, tzinfo=timezone.utc)}
    )
    interface_translation = InterfaceTranslationService(_Translator())
    handler = MemberLeftHandler(line, repo, service, interface_translation=interface_translation)

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

    assert len(line.messages) == 2
    group_message = line.messages[0][1]
    dm_message = line.messages[1][1]
    assert "ja:" in group_message
    assert "fr:" in group_message
    assert group_message.count("ja:") == 1
    assert "ja:" in dm_message
