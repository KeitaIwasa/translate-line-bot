from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from typing import Optional

from ..ports import MessageRepositoryPort

logger = logging.getLogger(__name__)


class SubscriptionService:
    """Stripe 連携と DB 同期を担うサブスク管理サービス。"""

    def __init__(
        self,
        repo: MessageRepositoryPort,
        stripe_secret_key: str,
        stripe_price_monthly_id: str,
        checkout_base_url: str = "",
    ) -> None:
        self._repo = repo
        self._stripe_secret_key = stripe_secret_key
        self._stripe_price_monthly_id = stripe_price_monthly_id
        self._checkout_base_url = checkout_base_url.rstrip("/") if checkout_base_url else ""

    def create_checkout_url(self, group_id: str) -> Optional[str]:
        stripe = self._load_stripe()
        if not stripe:
            return None
        if not self._stripe_secret_key or not self._stripe_price_monthly_id:
            return None

        stripe.api_key = self._stripe_secret_key
        try:
            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": self._stripe_price_monthly_id, "quantity": 1}],
                success_url="https://line.me/R/nv/chat",
                cancel_url="https://line.me/R/nv/chat",
                metadata={"group_id": group_id},
                subscription_data={"metadata": {"group_id": group_id}},
            )
            checkout_url = getattr(session, "url", None)
            session_id = getattr(session, "id", None)

            if self._checkout_base_url and session_id:
                # 事前案内ページへ遷移させ、ページ内ボタンから Checkout に進ませる
                return f"{self._checkout_base_url}/pages/index.html?session_id={session_id}"

            return checkout_url
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to create checkout session: %s", exc)
            return None

    def create_portal_url(self, group_id: str) -> Optional[str]:
        stripe = self._load_stripe()
        if not stripe:
            return None

        customer_id, _subscription_id, _status = getattr(self._repo, "get_subscription_detail", lambda *_: (None, None, None))(
            group_id
        )
        if not customer_id or not self._stripe_secret_key:
            return None

        stripe.api_key = self._stripe_secret_key
        return_url = self._checkout_base_url + "/portal-return" if self._checkout_base_url else "https://translate.iwasadigital.com/pages/thanks.html"
        try:
            session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
            return getattr(session, "url", None)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to create billing portal session", exc_info=True)
            return None

    def cancel_subscription(self, group_id: str) -> bool:
        stripe = self._load_stripe()
        if not stripe or not self._stripe_secret_key:
            return False

        customer_id, subscription_id, _status = getattr(self._repo, "get_subscription_detail", lambda *_: (None, None, None))(
            group_id
        )
        if not subscription_id or not customer_id:
            return False

        stripe.api_key = self._stripe_secret_key
        try:
            # まず即時キャンセルを試み、失敗した場合は課金終了日までキャンセル予約にフォールバックする
            try:
                subscription = stripe.Subscription.delete(subscription_id)
                cancelled_immediately = True
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Immediate cancel failed; fallback to cancel_at_period_end",
                    exc_info=True,
                    extra={"subscription_id": subscription_id},
                )
                subscription = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
                cancelled_immediately = False

            period_end_ts = subscription.get("current_period_end")
            period_end = datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if period_end_ts else None

            # Stripe は cancel_at_period_end=True の場合 status が active のままになるので、DB 上は canceled として扱う
            status = subscription.get("status") or "canceled"
            if not cancelled_immediately and subscription.get("cancel_at_period_end"):
                status = "canceled"

            updater = getattr(self._repo, "update_subscription_status", None)
            if updater:
                updater(group_id, status, period_end)

            # 解約後は翻訳を停止する
            set_enabled = getattr(self._repo, "set_translation_enabled", None)
            if set_enabled:
                set_enabled(group_id, False)

            return True
        except Exception:  # pylint: disable=broad-except
            logger.exception("Failed to cancel subscription", extra={"subscription_id": subscription_id})
            return False

    @staticmethod
    def build_subscription_summary_text(status: Optional[str], period_end: Optional[datetime]) -> str:
        if status in {"active", "trialing"}:
            suffix = ""
            if period_end:
                suffix = f" (renews on {period_end.date().isoformat()})"
            return f"Plan: Pro ({status}){suffix}"
        if status:
            return f"Plan: Free (status: {status})"
        return "Plan: Free (no subscription)"

    @staticmethod
    def _load_stripe():
        try:
            return importlib.import_module("stripe")
        except ModuleNotFoundError:
            logger.warning("stripe SDK not available")
            return None
