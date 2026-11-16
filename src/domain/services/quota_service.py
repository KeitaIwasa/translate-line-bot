from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

from ..ports import UsageRepositoryPort
from .plan_policy import FREE_PLAN, normalize_plan_key


@dataclass(frozen=True)
class QuotaDecision:
    """クオータ判定結果を表す DTO。"""

    allowed: bool
    should_notify: bool
    stop_translation: bool
    usage: int
    limit: int
    period_key: str
    plan_key: str


def quota_should_notify(
    *,
    allowed: bool,
    usage: int,
    limit: int,
    notice_plan: Optional[str],
    plan_key: str,
    stop_translation_on_limit: bool,
) -> bool:
    """上限通知を出すべきかを返す。

    無料プラン（上限で翻訳を止める）は、翻訳が実際に止まるブロック時だけ通知する。
    上限ちょうどの成功翻訳ではまだ訳すので、ここでは通知しない。
    そこでフラグを先に立てると、停止メッセージ側の should_notify が False になり、
    アップグレード案内が出ない。

    有料プランは従来どおり、上限到達の成功翻訳と、未記録のままブロックされたときに通知する。
    同一プランで記録済みなら再送しない。
    """

    if notice_plan == plan_key:
        return False
    if usage < limit:
        return False
    if stop_translation_on_limit:
        return not allowed
    return (not allowed) or usage == limit


class QuotaService:
    """課金状態と利用上限の判定を担当するサービス。"""

    def __init__(self, repo: UsageRepositoryPort) -> None:
        self._repo = repo

    def compute_period_key(
        self,
        *,
        plan_key: str,
        period_start: Optional[datetime],
        period_end: Optional[datetime],
        quota_anchor_day: Optional[int],
    ) -> str:
        """課金周期開始日をキーにする。Freeは暦月1日基準。"""
        now = datetime.now(timezone.utc)
        normalized_plan = normalize_plan_key(plan_key)
        if normalized_plan == FREE_PLAN:
            return f"{now.year:04d}-{now.month:02d}-01"

        anchor = period_start
        if not anchor and period_end:
            anchor = period_end - timedelta(days=31)
        if anchor:
            return anchor.astimezone(timezone.utc).date().isoformat()

        if quota_anchor_day:
            return self._period_key_by_anchor_day(now, quota_anchor_day)
        return f"{now.year:04d}-{now.month:02d}-01"

    def evaluate(
        self,
        *,
        group_id: str,
        plan_key: str,
        stop_translation_on_limit: bool,
        limit: int,
        period_start: Optional[datetime],
        period_end: Optional[datetime],
        quota_anchor_day: Optional[int],
        increment: int = 1,
    ) -> QuotaDecision:
        """利用可否と通知要否を判定する。

        MessageHandler の既存ロジックをそのまま集約し、戻り値で意図を伝える。
        - allowed: True の場合のみ翻訳を実行する
        - should_notify: 上限通知を出すべき場合。無料は翻訳停止時、有料は上限到達時
        - stop_translation: free プランで上限超過時に翻訳を停止すべきか
        """

        period_key = self.compute_period_key(
            plan_key=plan_key,
            period_start=period_start,
            period_end=period_end,
            quota_anchor_day=quota_anchor_day,
        )
        try:
            return self._repo.reserve_quota_slot(
                group_id=group_id,
                period_key=period_key,
                plan_key=plan_key,
                stop_translation_on_limit=stop_translation_on_limit,
                limit=limit,
                increment=increment,
            )
        except TypeError:
            # 後方互換: 旧実装向けに、translation_count ベースで同等判定を行う
            return self._evaluate_with_usage_counter(
                group_id=group_id,
                period_key=period_key,
                plan_key=plan_key,
                stop_translation_on_limit=stop_translation_on_limit,
                limit=limit,
                increment=increment,
            )

    @staticmethod
    def _period_key_by_anchor_day(now: datetime, anchor_day: int) -> str:
        normalized_day = min(max(int(anchor_day), 1), 31)
        current_day = now.day
        if current_day >= normalized_day:
            return f"{now.year:04d}-{now.month:02d}-{normalized_day:02d}"

        prev = (now.replace(day=1) - timedelta(days=1))
        safe_day = min(normalized_day, prev.day)
        return f"{prev.year:04d}-{prev.month:02d}-{safe_day:02d}"

    def _evaluate_with_usage_counter(
        self,
        *,
        group_id: str,
        period_key: str,
        plan_key: str,
        stop_translation_on_limit: bool,
        limit: int,
        increment: int,
    ) -> QuotaDecision:
        current_usage = self._repo.get_usage(group_id, period_key)
        notice_plan = self._repo.get_limit_notice_plan(group_id, period_key)
        if current_usage >= limit:
            usage_after = current_usage
            allowed = False
        else:
            usage_after = self._repo.increment_usage(group_id, period_key, increment)
            allowed = usage_after <= limit
        return QuotaDecision(
            allowed=allowed,
            should_notify=quota_should_notify(
                allowed=allowed,
                usage=usage_after,
                limit=limit,
                notice_plan=notice_plan,
                plan_key=plan_key,
                stop_translation_on_limit=stop_translation_on_limit,
            ),
            stop_translation=stop_translation_on_limit and not allowed,
            usage=usage_after,
            limit=limit,
            period_key=period_key,
            plan_key=plan_key,
        )

    def rollback(self, *, group_id: str, period_key: str, increment: int = 1) -> None:
        """翻訳失敗時に利用カウントを巻き戻す。

        `increment_usage` に負数を渡してカウントを調整する。永続化に失敗した場合は警告ログのみ出し、処理は続行する。
        """

        if increment <= 0:
            # 増分指定が不正な場合は何もしない
            return

        try:
            self._repo.increment_usage(group_id, period_key, -increment)
        except Exception:
            logger.warning(
                "Usage rollback failed | group=%s period=%s",
                group_id,
                period_key,
                exc_info=True,
            )
logger = logging.getLogger(__name__)
