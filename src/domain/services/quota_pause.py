"""quota 停止を、今の上限と比べて外すかどうかを一箇所で決める。"""

from __future__ import annotations

from .plan_policy import FREE_PLAN, PRO_PLAN, STANDARD_PLAN, monthly_quota_for, normalize_plan_key, resolve_effective_plan


def effective_plan_key(status: str | None, entitlement_plan: str | None) -> str:
    """有効プラン。active/trialing でプラン未保存のときは Pro。"""

    plan_key = resolve_effective_plan(status, entitlement_plan)
    if status in {"active", "trialing"} and plan_key == FREE_PLAN:
        return PRO_PLAN
    return plan_key


def monthly_limit_for_plan(
    plan_key: str,
    *,
    free_quota: int,
    standard_quota: int,
    pro_quota: int,
) -> int:
    """上限判定と同じ、設定済みの月間件数。"""

    normalized = normalize_plan_key(plan_key)
    if normalized == FREE_PLAN:
        return free_quota
    if normalized == STANDARD_PLAN:
        return standard_quota
    if normalized == PRO_PLAN:
        return pro_quota
    return monthly_quota_for(normalized)


def reevaluate_quota_pause(*, paused_reason: str | None, usage: int, limit: int) -> bool:
    """quota 停止を外すなら True。

    理由が quota 以外の停止は変えない。まだ上限以上なら停止したまま。
    """

    if paused_reason != "quota":
        return False
    return int(usage) < int(limit)
