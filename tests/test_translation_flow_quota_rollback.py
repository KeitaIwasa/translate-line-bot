import pytest
from datetime import datetime, timezone

from src.domain import models
from src.domain.services.translation_flow_service import TranslationFlowService
from src.domain.services.quota_service import QuotaService
from src.domain.services.translation_service import TranslationService
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.ports import MessageRepositoryPort, UsageRepositoryPort


class DummyUsageRepo(UsageRepositoryPort):
    def __init__(self):
        self.usage = 0
        self.notice_plan = None

    # UsageRepositoryPort
    def increment_usage(self, group_id: str, period_key: str, increment: int = 1) -> int:
        self.usage += increment
        return self.usage

    def get_usage(self, group_id: str, period_key: str) -> int:
        return self.usage

    def get_limit_notice_plan(self, group_id: str, period_key: str):
        return self.notice_plan

    def set_limit_notice_plan(self, group_id: str, period_key: str, plan: str) -> None:
        self.notice_plan = plan

    def reset_limit_notice_plan(self, group_id: str) -> None:
        self.notice_plan = None


class DummyMessageRepo(MessageRepositoryPort):
    def fetch_recent_messages(self, group_id, limit):
        return []

    # unused MessageRepositoryPort methods
    def insert_message(self, *_args, **_kwargs):
        return None

    def ensure_group_member(self, *_args, **_kwargs):
        return None

    def try_complete_group_languages(self, *_args, **_kwargs):
        return False

    def record_language_prompt(self, *_args, **_kwargs):
        return None

    def try_cancel_language_prompt(self, *_args, **_kwargs):
        return False

    def reset_group_language_settings(self, *_args, **_kwargs):
        return None

    def fetch_group_languages(self, *_args, **_kwargs):
        return []

    def add_group_languages(self, *_args, **_kwargs):
        return None

    def remove_group_languages(self, *_args, **_kwargs):
        return None

    def record_bot_joined_at(self, *_args, **_kwargs):
        return None

    def fetch_bot_joined_at(self, *_args, **_kwargs):
        return None

    def set_translation_enabled(self, *_args, **_kwargs):
        return None

    def is_translation_enabled(self, *_args, **_kwargs):
        return True

    def upsert_group_name(self, *_args, **_kwargs):
        return None


class FailingTranslation(TranslationService):
    def __init__(self):
        pass

    def translate(self, *_, **__):
        raise RuntimeError("boom")


class EmptyTranslation(TranslationService):
    def __init__(self):
        pass

    def translate(self, *_, **__):
        return []


class NullInterfaceTranslation(InterfaceTranslationService):
    def __init__(self):
        pass

    def translate(self, *_, **__):
        return []


def _build_event(text: str = "hi"):
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
        text=text,
        user_id="user",
        group_id="group",
        sender_type="group",
    )


def _build_service(translator: TranslationService):
    usage_repo = DummyUsageRepo()
    quota = QuotaService(usage_repo)
    repo = DummyMessageRepo()
    interface_translation = NullInterfaceTranslation()

    service = TranslationFlowService(
        repo=repo,
        translation_service=translator,
        interface_translation=interface_translation,
        quota_service=quota,
        max_context_messages=1,
        translation_retry=1,
    )
    return service, usage_repo, quota


def test_usage_rollback_on_exception():
    service, usage_repo, quota = _build_service(FailingTranslation())
    period_start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(RuntimeError):
        service.run(
            event=_build_event(),
            sender_name="user",
            candidate_languages=["en"],
            paid=True,
            limit=5,
            plan_key="pro",
            period_start=period_start,
            period_end=period_start,
        )

    assert usage_repo.usage == 0


def test_usage_rollback_on_empty_response():
    service, usage_repo, quota = _build_service(EmptyTranslation())
    period_start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    result = service.run(
        event=_build_event(),
        sender_name="user",
        candidate_languages=["en"],
        paid=True,
        limit=5,
        plan_key="pro",
        period_start=period_start,
        period_end=period_start,
    )

    assert result.reply_text is None
    assert usage_repo.usage == 0
