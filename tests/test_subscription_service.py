import sys
import types
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest

from src.domain.services.subscription_service import SubscriptionService


def _fake_stripe_module(modify_result=None):
    """Stripe モジュールの簡易モックを生成する。"""
    module = types.ModuleType("stripe")

    class Subscription:
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


def test_cancel_subscription_reserves_cancellation_and_keeps_translation_enabled(monkeypatch):
    repo = _RepoStub()
    stripe_mod = _fake_stripe_module(
        modify_result={"status": "active", "cancel_at_period_end": True, "current_period_end": 1_700_000_000}
    )
    monkeypatch.setitem(sys.modules, "stripe", stripe_mod)

    service = SubscriptionService(repo, stripe_secret_key="sk_test", stripe_price_monthly_id="price_123")

    assert service.cancel_subscription("gid") is True
    assert repo.updated[0][1] == "active"
    # 予約解約では翻訳停止はしない
    assert repo.enabled_calls == []
    # period_end が datetime に変換されていること
    assert isinstance(repo.updated[0][2], datetime)


def test_cancel_subscription_returns_false_when_modify_fails(monkeypatch):
    repo = _RepoStub()
    stripe_mod = _fake_stripe_module()
    original_modify = stripe_mod.Subscription.modify

    def _raise(*_args, **_kwargs):
        raise Exception("modify failed")

    stripe_mod.Subscription.modify = _raise
    monkeypatch.setitem(sys.modules, "stripe", stripe_mod)

    service = SubscriptionService(repo, stripe_secret_key="sk_test", stripe_price_monthly_id="price_123")

    assert service.cancel_subscription("gid") is False
    assert repo.updated == []
    assert repo.enabled_calls == []
    stripe_mod.Subscription.modify = original_modify


def test_build_subscription_summary_text_for_standard_plan():
    period_end = datetime(2026, 2, 28, 0, 0)
    text = SubscriptionService.build_subscription_summary_text(
        "active",
        period_end,
        plan_key="standard",
    )
    assert text == "Plan: Standard (active) (renews on 2026-02-28)"


def test_build_subscription_summary_text_defaults_to_pro_when_plan_unspecified():
    text = SubscriptionService.build_subscription_summary_text("active", None)
    assert text == "Plan: Pro (active)"


def test_create_checkout_url_token_flow_has_only_signed_token():
    repo = _RepoStub()
    service = SubscriptionService(
        repo,
        stripe_secret_key="sk_test",
        stripe_price_monthly_id="price_123",
        subscription_frontend_base_url="https://kotori-ai.com",
        checkout_api_base_url="https://api.example.com/prod",
        subscription_token_secret="secret",
    )

    url = service.create_checkout_url("gid_1")
    assert url is not None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.path == "/pro.html"
    assert "st" in query and query["st"][0]
    assert "api_base" not in query


def test_create_checkout_url_token_flow_still_works_without_api_base():
    repo = _RepoStub()
    service = SubscriptionService(
        repo,
        stripe_secret_key="sk_test",
        stripe_price_monthly_id="price_123",
        subscription_frontend_base_url="https://kotori-ai.com",
        checkout_api_base_url="",
        subscription_token_secret="secret",
    )

    url = service.create_checkout_url("gid_2")
    assert url is not None
    query = parse_qs(urlparse(url).query)
    assert "st" in query and query["st"][0]
    assert "api_base" not in query
