import sys
import types
from datetime import datetime

import pytest

from src.domain.services.subscription_service import SubscriptionService


def _fake_stripe_module(delete_result=None, modify_result=None, delete_raises=False):
    """Stripe モジュールの簡易モックを生成する。"""
    module = types.ModuleType("stripe")

    class Subscription:
        @staticmethod
        def delete(_subscription_id):
            if delete_raises:
                raise Exception("delete failed")
            return delete_result or {}

        @staticmethod
        def modify(_subscription_id, cancel_at_period_end=True):  # noqa: ARG003
            return modify_result or {}

    module.Subscription = Subscription
    module.http_client = types.SimpleNamespace(RequestsClient=lambda: None)
    module.default_http_client = None
    return module


class _RepoStub:
    def __init__(self):
        self.updated = []
        self.enabled_calls = []

    def get_subscription_detail(self, group_id):
        return ("cust_123", "sub_123", "active")

    def update_subscription_status(self, group_id, status, period_end):
        self.updated.append((group_id, status, period_end))

    def set_translation_enabled(self, group_id, enabled):
        self.enabled_calls.append((group_id, enabled))


@pytest.fixture(autouse=True)
def cleanup_stripe(monkeypatch):
    """テストごとに stripe モジュールのモックを差し替える。"""
    original = sys.modules.pop("stripe", None)
    yield
    if original is not None:
        sys.modules["stripe"] = original


def test_cancel_subscription_updates_status_and_disables_translation(monkeypatch):
    repo = _RepoStub()
    stripe_mod = _fake_stripe_module(delete_result={"status": "canceled", "current_period_end": 1_700_000_000})
    monkeypatch.setitem(sys.modules, "stripe", stripe_mod)

    service = SubscriptionService(repo, stripe_secret_key="sk_test", stripe_price_monthly_id="price_123")

    assert service.cancel_subscription("gid") is True
    assert repo.updated[0][1] == "canceled"
    # 翻訳停止が呼ばれること
    assert repo.enabled_calls == [("gid", False)]
    # period_end が datetime に変換されていること
    assert isinstance(repo.updated[0][2], datetime)


def test_cancel_subscription_fallback_sets_canceled_when_active_at_period_end(monkeypatch):
    repo = _RepoStub()
    # delete が失敗し、modify が active + cancel_at_period_end=True を返すケース
    stripe_mod = _fake_stripe_module(
        delete_raises=True,
        modify_result={"status": "active", "cancel_at_period_end": True, "current_period_end": 1_700_000_000},
    )
    monkeypatch.setitem(sys.modules, "stripe", stripe_mod)

    service = SubscriptionService(repo, stripe_secret_key="sk_test", stripe_price_monthly_id="price_123")

    assert service.cancel_subscription("gid") is True
    # DB には canceled として記録する
    assert repo.updated[0][1] == "canceled"
    assert repo.enabled_calls == [("gid", False)]
