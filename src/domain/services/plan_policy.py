from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


PlanKey = str
BillingInterval = str

FREE_PLAN = "free"
STANDARD_PLAN = "standard"
PRO_PLAN = "pro"

BILLING_MONTH = "month"
BILLING_YEAR = "year"
BILLING_LEGACY_MONTH = "legacy_month"


@dataclass(frozen=True)
class PlanDefinition:
    key: PlanKey
    monthly_quota: int
    language_limit: int


@dataclass(frozen=True)
class PriceDefinition:
    key: str
    plan: PlanKey
    interval: BillingInterval
    is_grandfathered: bool = False


PLAN_DEFINITIONS: Dict[PlanKey, PlanDefinition] = {
    FREE_PLAN: PlanDefinition(key=FREE_PLAN, monthly_quota=50, language_limit=3),
    STANDARD_PLAN: PlanDefinition(key=STANDARD_PLAN, monthly_quota=4_000, language_limit=3),
    PRO_PLAN: PlanDefinition(key=PRO_PLAN, monthly_quota=40_000, language_limit=5),
}

PRICE_DEFINITIONS: Dict[str, PriceDefinition] = {
    "standard_monthly": PriceDefinition("standard_monthly", STANDARD_PLAN, BILLING_MONTH),
    "standard_yearly": PriceDefinition("standard_yearly", STANDARD_PLAN, BILLING_YEAR),
    "pro_monthly": PriceDefinition("pro_monthly", PRO_PLAN, BILLING_MONTH),
    "pro_yearly": PriceDefinition("pro_yearly", PRO_PLAN, BILLING_YEAR),
    "pro_legacy_monthly": PriceDefinition(
        "pro_legacy_monthly",
        PRO_PLAN,
        BILLING_LEGACY_MONTH,
        is_grandfathered=True,
    ),
}

TARGET_ORDER = (
    "standard_monthly",
    "standard_yearly",
    "pro_monthly",
    "pro_yearly",
)


def normalize_plan_key(plan_key: Optional[str]) -> PlanKey:
    lowered = (plan_key or "").strip().lower()
    if lowered in PLAN_DEFINITIONS:
        return lowered
    return FREE_PLAN


def monthly_quota_for(plan_key: Optional[str]) -> int:
    plan = PLAN_DEFINITIONS[normalize_plan_key(plan_key)]
    return plan.monthly_quota


def language_limit_for(plan_key: Optional[str]) -> int:
    plan = PLAN_DEFINITIONS[normalize_plan_key(plan_key)]
    return plan.language_limit


def resolve_effective_plan(subscription_status: Optional[str], entitlement_plan: Optional[str]) -> PlanKey:
    """購読状態から有効プランを解決する。"""
    if subscription_status in {"active", "trialing"}:
        return normalize_plan_key(entitlement_plan)
    return FREE_PLAN


def stop_translation_on_quota(plan_key: Optional[str]) -> bool:
    """上限超過時の翻訳停止判定。"""
    normalized = normalize_plan_key(plan_key)
    return normalized in {FREE_PLAN, STANDARD_PLAN, PRO_PLAN}


def parse_target_price_key(value: Optional[str]) -> Optional[str]:
    key = (value or "").strip().lower()
    if key in TARGET_ORDER:
        return key
    return None


def price_def_by_target(target_key: str) -> PriceDefinition:
    return PRICE_DEFINITIONS[target_key]
