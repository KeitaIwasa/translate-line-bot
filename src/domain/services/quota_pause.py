"""翻訳の停止状態を、一箇所の遷移で決める。

状態は (translation_enabled, translation_paused_reason) だけ。

- 翻訳中: enabled、理由なし
- 手動停止: disabled、理由なし
- 無料枠停止: disabled、quota
- リセット案内の未送信: enabled、quota
- 課金停止: disabled、billing

案内を「送った」と記録するのは、LINE 送信が成功したあとだけ。
"""

from __future__ import annotations

from dataclasses import dataclass

from .plan_policy import FREE_PLAN, PRO_PLAN, STANDARD_PLAN, monthly_quota_for, normalize_plan_key, resolve_effective_plan

QUOTA_PAUSE = "quota"
BILLING_PAUSE = "billing"


@dataclass(frozen=True)
class PauseState:
    """group_settings に書く停止状態。"""

    enabled: bool
    reason: str | None


def pause_for_billing(state: PauseState) -> PauseState:
    """支払い失敗・解約・未払い。手動停止と quota 停止は上書きしない。

    翻訳中だけ billing にする。リセット案内待ち（enabled かつ quota）は、
    翻訳を止めて quota のまま残す。案内のやり直し口を消さない。
    """

    if state.reason == QUOTA_PAUSE:
        return PauseState(enabled=False, reason=QUOTA_PAUSE)
    if not state.enabled and state.reason in {None, BILLING_PAUSE}:
        return state
    return PauseState(enabled=False, reason=BILLING_PAUSE)


def clear_billing_pause(state: PauseState) -> PauseState:
    """billing だけ翻訳を戻す。手動停止と quota 停止は変えない。"""

    if state.reason != BILLING_PAUSE:
        return state
    return PauseState(enabled=True, reason=None)


def recover_pause(state: PauseState, *, usage: int, limit: int) -> PauseState:
    """支払い成功・再契約・有料化のあと。

    billing を外してから、quota は今の上限未満のときだけ外す。
    """

    cleared = clear_billing_pause(state)
    if reevaluate_quota_pause(paused_reason=cleared.reason, usage=usage, limit=limit):
        return PauseState(enabled=True, reason=None)
    return cleared


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
