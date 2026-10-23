"""quota 停止は、上限の変わり目で同じ再判定だけが外す。"""

import types
from datetime import datetime, timezone

from src.domain.models import TranslationRuntimeState
from src.domain.services.plan_policy import BILLING_MONTH, STANDARD_PLAN, PriceDefinition
from src.domain.services.quota_pause import reevaluate_quota_pause
from tests.test_quota_limits import (
    ProQuotaRepo,
    RecordingLineClient,
    RecordingTranslationService,
    _build_event,
    _build_handler,
)
from tests.test_stripe_webhook_billing_owner import _import_module


def test_reevaluate_lifts_only_a_quota_pause_under_the_limit():
    assert reevaluate_quota_pause(paused_reason="quota", usage=50, limit=4000) is True
    assert reevaluate_quota_pause(paused_reason="quota", usage=50, limit=50) is False
    assert reevaluate_quota_pause(paused_reason=None, usage=0, limit=50) is False
    assert reevaluate_quota_pause(paused_reason="manual", usage=0, limit=50) is False


def test_paid_message_lifts_a_quota_pause_without_the_free_reset_notice():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(
        initial_usage=50,
        paid=True,
        translation_enabled=False,
        paused_reason="quota",
    )
    handler = _build_handler(repo, line, translation)

    handler._handle_translation_flow(_build_event("hello"), sender_name="user", translation_enabled=False)

    assert translation.calls == 1
    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert "has reset" not in "\n".join(line.sent_texts)


class _SubscriptionRepo:
    def __init__(self, *, paused_reason, enabled):
        self.paused_reason = paused_reason
        self.enabled = enabled
        self.status = None
        self.entitlement_plan = "free"
        self.period_start = None
        self.period_end = None
        self.quota_anchor_day = None
        self.usage = 50
        self.cleared_renewal = False
        self.enable_calls = 0

    def upsert_subscription(self, group_id, stripe_customer_id, stripe_subscription_id, status, current_period_start, current_period_end, **kwargs):
        self.status = status
        self.entitlement_plan = kwargs.get("entitlement_plan") or "free"
        self.period_start = current_period_start
        self.period_end = current_period_end
        self.quota_anchor_day = kwargs.get("quota_anchor_day")

    def fetch_translation_runtime_state(self, _group_id):
        period_key = ""
        if self.period_start is not None:
            period_key = self.period_start.astimezone(timezone.utc).date().isoformat()
        return TranslationRuntimeState(
            translation_enabled=self.enabled,
            group_languages=["en"],
            subscription_status=self.status,
            period_start=self.period_start,
            period_end=self.period_end,
            period_key=period_key,
            usage=self.usage,
            limit_notice_plan=None,
            entitlement_plan=self.entitlement_plan,
            quota_anchor_day=self.quota_anchor_day,
            translation_paused_reason=self.paused_reason,
        )

    def get_usage(self, _group_id, _period_key):
        return self.usage

    def set_translation_enabled(self, _group_id, enabled):
        self.enable_calls += 1
        self.enabled = enabled
        if enabled:
            self.paused_reason = None

    def get_renewal_reservation(self, _group_id):
        return (False, None, None, None, None, None, None, None)

    def clear_renewal_reservation(self, _group_id):
        self.cleared_renewal = True

    def get_billing_owner_claim_state(self, _group_id):
        return (None, None, None, None, None)


def _standard_subscription(*, flow_type=None):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    metadata = {"group_id": "gid", "line_user_id": "U1"}
    if flow_type:
        metadata["flow_type"] = flow_type
    return {
        "id": "sub_1",
        "customer": "cus_1",
        "status": "active",
        "current_period_start": int(start.timestamp()),
        "current_period_end": int(end.timestamp()),
        "metadata": metadata,
        "items": {"data": [{"price": {"id": "price_standard"}}]},
    }


def _stub_catalog(monkeypatch, module):
    monkeypatch.setattr(
        module,
        "price_catalog",
        types.SimpleNamespace(
            resolve_price=lambda _price_id: PriceDefinition("standard_monthly", STANDARD_PLAN, BILLING_MONTH)
        ),
    )
    monkeypatch.setattr(module, "_push_payment_confirmation", lambda _group_id: None)


def test_checkout_upgrade_lifts_a_quota_pause(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason="quota", enabled=False)
    subscription = _standard_subscription()
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    _stub_catalog(monkeypatch, module)
    module.stripe.checkout = types.SimpleNamespace(
        Session=types.SimpleNamespace(
            retrieve=lambda _session_id, expand=None: {
                "id": "cs_1",
                "mode": "subscription",
                "metadata": {"group_id": "gid"},
                "subscription": subscription,
            }
        )
    )

    module._handle_checkout_session_completed({"id": "cs_1"}, int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()))

    assert repo.entitlement_plan == "standard"
    assert repo.enabled is True
    assert repo.paused_reason is None
    assert repo.enable_calls == 1


def test_renewal_payment_lifts_a_quota_pause(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason="quota", enabled=False)
    subscription = _standard_subscription(flow_type="renewal_setup")
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    _stub_catalog(monkeypatch, module)
    module.stripe.Subscription = types.SimpleNamespace(retrieve=lambda _subscription_id: subscription)

    module._handle_payment_succeeded(
        {"id": "in_1", "subscription": "sub_1"},
        int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()),
    )

    assert repo.cleared_renewal is True
    assert repo.enabled is True
    assert repo.paused_reason is None
    assert repo.enable_calls == 1


def test_upgrade_webhook_leaves_a_manual_pause(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason=None, enabled=False)
    subscription = _standard_subscription()
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    _stub_catalog(monkeypatch, module)

    module._handle_subscription_updated(subscription, int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()))

    assert repo.entitlement_plan == "standard"
    assert repo.enabled is False
    assert repo.paused_reason is None
    assert repo.enable_calls == 0
