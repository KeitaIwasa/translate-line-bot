from __future__ import annotations

import importlib
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from typing import Optional

from ..ports import MessageRepositoryPort
from ...infra.signed_token import issue_token

logger = logging.getLogger(__name__)


class SubscriptionService:
    """Stripe 連携と DB 同期を担うサブスク管理サービス。"""

    def __init__(
        self,
        repo: MessageRepositoryPort,
        stripe_secret_key: str,
        stripe_price_monthly_id: str,
        subscription_frontend_base_url: str = "",
        checkout_api_base_url: str = "",
        subscription_token_secret: str = "",
    ) -> None:
        self._repo = repo
        self._stripe_secret_key = stripe_secret_key
        self._stripe_price_monthly_id = stripe_price_monthly_id
        # 案内ポータル (GitHub Pages など) のベース URL
        self._subscription_frontend_base_url = subscription_frontend_base_url.rstrip("/") if subscription_frontend_base_url else ""
        # API Gateway 側のベース URL (/checkout リダイレクトを提供)
        self._checkout_api_base_url = checkout_api_base_url.rstrip("/") if checkout_api_base_url else ""
        self._subscription_token_secret = subscription_token_secret or ""

    def create_checkout_url(self, group_id: str) -> Optional[str]:
        token_url = self._build_plan_url(group_id, scope="checkout")
        if token_url:
            return token_url
        # 後方互換: 旧設定では session_id 導線を返す
        return self._create_legacy_checkout_url(group_id)

    def create_support_contact_url(self, group_id: str) -> Optional[str]:
        return self._build_plan_url(group_id, scope="support", page_path="/contact.html")

    def _build_plan_url(self, group_id: str, *, scope: str, page_path: str = "/pro.html") -> Optional[str]:
        if not group_id:
            return None
        if not self._subscription_frontend_base_url or not self._subscription_token_secret:
            return None
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        token = issue_token(
            {
                "version": 2,
                "group_id": group_id,
                "scope": scope,
                "exp": int(expires_at.timestamp()),
            },
            secret=self._subscription_token_secret,
        )
        return f"{self._subscription_frontend_base_url}{page_path}?st={quote_plus(token)}"

    def _create_legacy_checkout_url(self, group_id: str) -> Optional[str]:
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
                cancel_url=self._build_cancel_url(),
                metadata={"group_id": group_id},
                subscription_data={"metadata": {"group_id": group_id}},
            )
            checkout_url = getattr(session, "url", None)
            session_id = getattr(session, "id", None)

            if self._subscription_frontend_base_url and session_id:
                # 事前案内ページへ遷移させ、ページ内ボタンから Checkout に進ませる
                # /api 経由の同一オリジン呼び出しに統一するため、api_base クエリは付与しない。
                checkout_param = ""
                if (not self._checkout_api_base_url) and checkout_url:
                    checkout_param = f"&checkout_url={quote_plus(checkout_url)}"

                return f"{self._subscription_frontend_base_url}/pro.html?session_id={session_id}{checkout_param}"

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
        # ポータル遷移後の戻り先
        return_url = (
            self._subscription_frontend_base_url + "/portal-return"
            if self._subscription_frontend_base_url
            else "https://line.me/R/nv/chat"
        )
        try:
            session = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
            return getattr(session, "url", None)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to create billing portal session", exc_info=True)
            return None

    def _build_cancel_url(self) -> str:
        """Stripe Checkout のキャンセル遷移先を組み立てる。"""
        if not self._subscription_frontend_base_url:
            return "https://line.me/R/nv/chat"

        # {CHECKOUT_SESSION_ID} は Stripe 側で実セッション ID に置換される
        return (
            f"{self._subscription_frontend_base_url}/pro.html"
            f"?session_id={{CHECKOUT_SESSION_ID}}"
        )

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
            # 解約は常に次回更新時に適用する（期間中の権利は維持）
            subscription = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)

            period_end_ts = subscription.get("current_period_end")
            period_end = datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if period_end_ts else None

            # 予約解約中は Stripe status が active/trialing のままなのでそのまま保存する。
            status = subscription.get("status") or "active"

            updater = getattr(self._repo, "update_subscription_status", None)
            if updater:
                updater(group_id, status, period_end)

            return True
        except Exception:  # pylint: disable=broad-except
            logger.exception("Failed to cancel subscription", extra={"subscription_id": subscription_id})
            return False

    @staticmethod
    def build_subscription_summary_text(
        status: Optional[str],
        period_end: Optional[datetime],
        *,
        plan_key: Optional[str] = None,
    ) -> str:
        normalized_plan = (plan_key or "").strip().lower()
        plan_label = "Pro"
        if normalized_plan == "standard":
            plan_label = "Standard"
        elif normalized_plan == "free":
            plan_label = "Free"

        if status in {"active", "trialing"}:
            suffix = ""
            if period_end:
                suffix = f" (renews on {period_end.date().isoformat()})"
            return f"Plan: {plan_label} ({status}){suffix}"
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
