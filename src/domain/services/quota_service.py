from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

from ..ports import UsageRepositoryPort


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


class QuotaService:
    """課金状態と利用上限の判定を担当するサービス。"""

    def __init__(self, repo: UsageRepositoryPort) -> None:
        self._repo = repo

    def compute_period_key(
        self, *, paid: bool, period_start: Optional[datetime], period_end: Optional[datetime]
    ) -> str:
        """課金周期開始日をキーにする。未課金は暦月1日基準。"""
        now = datetime.now(timezone.utc)
        if paid:
            anchor = period_start
            if not anchor and period_end:
                anchor = period_end - timedelta(days=31)
            if anchor:
                return anchor.astimezone(timezone.utc).date().isoformat()
        return f"{now.year:04d}-{now.month:02d}-01"

    def evaluate(
        self,
        *,
        group_id: str,
        paid: bool,
        limit: int,
        period_start: Optional[datetime],
        period_end: Optional[datetime],
        plan_key: str,
        increment: int = 1,
    ) -> QuotaDecision:
        """利用可否と通知要否を判定する。

        MessageHandler の既存ロジックをそのまま集約し、戻り値で意図を伝える。
        - allowed: True の場合のみ翻訳を実行する
        - should_notify: 上限到達を通知すべき場合（allowed=True なら翻訳後に通知）
        - stop_translation: free プランで上限超過時に翻訳を停止すべきか
        """

        period_key = self.compute_period_key(
            paid=paid,
            period_start=period_start,
            period_end=period_end,
        )
        return self._repo.reserve_quota_slot(
            group_id=group_id,
            period_key=period_key,
            plan_key=plan_key,
            paid=paid,
            limit=limit,
            increment=increment,
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
