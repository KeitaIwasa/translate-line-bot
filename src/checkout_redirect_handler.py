from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .config import get_settings
from .domain.services.plan_policy import (
    FREE_PLAN,
    PRO_PLAN,
    STANDARD_PLAN,
    parse_target_price_key,
)
from .domain.services.quota_service import QuotaService
from .infra.neon_client import get_client
from .infra.neon_repositories import NeonMessageRepository
from .infra.signed_token import TokenError, verify_token
from .infra.stripe_price_catalog import build_price_catalog

logger = logging.getLogger(__name__)
settings = get_settings()
_repo: Optional[NeonMessageRepository] = None


def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    mode = _extract_mode(event)

    if mode == "status":
        return _handle_status(event)
    if mode == "start":
        return _handle_start(event)
    if mode == "portal":
        return _handle_portal(event)

    # 互換: 旧 session_id リダイレクト
    session_id = _extract_session_id(event)
    if session_id:
        return _legacy_redirect(session_id)

    return _json_response(400, {"message": "mode is required"})


def _handle_status(event: Dict[str, Any]) -> Dict[str, Any]:
    token = _extract_token(event)
    if token:
        try:
            payload = verify_token(token, secret=settings.subscription_token_secret, scope="checkout")
        except TokenError:
            return _json_response(401, {"message": "invalid token"})

        group_id = str(payload.get("group_id") or "").strip()
        if not group_id:
            return _json_response(400, {"message": "token missing group_id"})
        return _status_from_db(group_id)

    # 互換: 旧 session_id ベース
    session_id = _extract_session_id(event)
    if session_id:
        return _legacy_status_response(session_id)

    return _json_response(400, {"message": "st is required"})


def _handle_start(event: Dict[str, Any]) -> Dict[str, Any]:
    token = _extract_token(event)
    if not token:
        return _json_response(400, {"message": "st is required"})

    target_raw = _extract_target(event)
    target = parse_target_price_key(target_raw)
    if not target:
        return _json_response(400, {"message": "target is invalid"})

    try:
        payload = verify_token(token, secret=settings.subscription_token_secret, scope="checkout")
    except TokenError:
        return _json_response(401, {"message": "invalid token"})

    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        return _json_response(400, {"message": "token missing group_id"})

    stripe = _import_stripe()
    if not stripe or not settings.stripe_secret_key:
        logger.warning("Stripe SDK unavailable or secret not set")
        return _json_response(503, {"message": "Stripe is not available"})

    catalog = build_price_catalog(settings)
    target_price_id = catalog.resolve_target(target)
    if not target_price_id:
        return _json_response(400, {"message": "target price is not configured"})

    stripe.api_key = settings.stripe_secret_key
    repo = _get_repo()

    customer_id, subscription_id, status = repo.get_subscription_detail(group_id)
    is_active = status in {"active", "trialing"}

    # 新規: Checkout Session を作成して URL を返す
    if (not customer_id) or (not subscription_id) or (not is_active):
        session_url = _create_checkout_session(
            stripe=stripe,
            group_id=group_id,
            price_id=target_price_id,
            customer_id=customer_id,
        )
        if not session_url:
            return _json_response(500, {"message": "failed to create checkout session"})
        return _json_response(200, {"mode": "start", "result": "checkout_created", "redirectUrl": session_url})

    # 既存契約: 変更ポリシー（アップグレード即時 / ダウングレード次回）
    try:
        subscription = stripe.Subscription.retrieve(subscription_id, expand=["items.data.price"])
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve subscription for change: %s", exc)
        return _json_response(404, {"message": "subscription not found"})

    current_price_id, current_item_id = _extract_subscription_item(subscription)
    current_plan = _resolve_current_plan(repo, group_id, catalog, current_price_id)
    target_plan = _target_plan_from_key(target)

    if current_price_id == target_price_id:
        return _json_response(200, {"mode": "start", "result": "already_current"})

    is_upgrade = _plan_rank(target_plan) > _plan_rank(current_plan)

    if is_upgrade:
        hosted_url = _create_upgrade_hosted_url(
            stripe=stripe,
            customer_id=customer_id,
            subscription_id=subscription_id,
            item_id=current_item_id,
            target_price_id=target_price_id,
        )
        if not hosted_url:
            return _json_response(500, {"message": "failed to create upgrade checkout session"})
        return _json_response(200, {"mode": "start", "result": "checkout_created", "redirectUrl": hosted_url})

    scheduled_at = _schedule_subscription_change(
        stripe=stripe,
        subscription=subscription,
        subscription_id=subscription_id,
        target_price_id=target_price_id,
    )
    if not scheduled_at:
        return _json_response(500, {"message": "failed to schedule change"})

    period_start = _to_datetime(subscription.get("current_period_start"))
    period_end = _to_datetime(subscription.get("current_period_end"))
    quota_anchor_day = (period_start or period_end or datetime.now(timezone.utc)).day

    # DB は scheduled_* を更新して UI から参照できるようにする
    price_def = catalog.resolve_price(current_price_id)
    entitlement_plan = (price_def.plan if price_def else current_plan)
    billing_interval = (price_def.interval if price_def else "month")
    is_grandfathered = bool(price_def.is_grandfathered) if price_def else False
    try:
        repo.upsert_subscription(
            group_id,
            stripe_customer_id=customer_id or "",
            stripe_subscription_id=subscription_id,
            status=subscription.get("status") or status or "active",
            current_period_start=period_start,
            current_period_end=period_end,
            stripe_price_id=current_price_id,
            entitlement_plan=entitlement_plan,
            billing_interval=billing_interval,
            is_grandfathered=is_grandfathered,
            quota_anchor_day=quota_anchor_day,
            scheduled_target_price_id=target_price_id,
            scheduled_effective_at=scheduled_at,
        )
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to persist scheduled change to DB", exc_info=True)

    return _json_response(
        200,
        {
            "mode": "start",
            "result": "scheduled",
            "scheduledEffectiveAt": scheduled_at.isoformat().replace("+00:00", "Z"),
        },
    )


def _handle_portal(event: Dict[str, Any]) -> Dict[str, Any]:
    token = _extract_token(event)
    if not token:
        return _json_response(400, {"message": "st is required"})

    try:
        payload = verify_token(token, secret=settings.subscription_token_secret, scope="checkout")
    except TokenError:
        return _json_response(401, {"message": "invalid token"})

    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        return _json_response(400, {"message": "token missing group_id"})

    stripe = _import_stripe()
    if not stripe or not settings.stripe_secret_key:
        logger.warning("Stripe SDK unavailable or secret not set")
        return _json_response(503, {"message": "Stripe is not available"})

    repo = _get_repo()
    customer_id, _subscription_id, _status = repo.get_subscription_detail(group_id)
    if not customer_id:
        return _json_response(404, {"message": "customer not found"})

    stripe.api_key = settings.stripe_secret_key
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url="https://line.me/R/nv/chat",
        )
        redirect_url = getattr(session, "url", None)
        if not redirect_url:
            raise ValueError("portal session missing url")
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to create billing portal session", exc_info=True)
        return _json_response(500, {"message": "failed to create billing portal session"})

    return _json_response(
        200,
        {
            "mode": "portal",
            "result": "portal_created",
            "redirectUrl": redirect_url,
        },
    )


def _status_from_db(group_id: str) -> Dict[str, Any]:
    repo = _get_repo()
    (
        status,
        entitlement_plan,
        billing_interval,
        is_grandfathered,
        stripe_price_id,
        current_period_start,
        current_period_end,
        quota_anchor_day,
        scheduled_target_price_id,
        scheduled_effective_at,
    ) = repo.get_subscription_plan(group_id)

    effective_plan = FREE_PLAN
    if status in {"active", "trialing"}:
        effective_plan = entitlement_plan or FREE_PLAN
        if effective_plan == FREE_PLAN:
            # 後方互換: entitlement 未保存環境
            effective_plan = PRO_PLAN

    quota = QuotaService(repo)
    period_key = quota.compute_period_key(
        plan_key=effective_plan,
        period_start=current_period_start,
        period_end=current_period_end,
        quota_anchor_day=quota_anchor_day,
    )
    translation_count = repo.get_usage(group_id, period_key)

    body = {
        "groupId": group_id,
        "subscriptionStatus": status,
        "entitlementPlan": entitlement_plan,
        "effectivePlan": effective_plan,
        "periodKey": period_key,
        "translationCount": translation_count,
        "billingInterval": billing_interval,
        "isGrandfathered": bool(is_grandfathered),
        "stripePriceId": stripe_price_id,
        "currentPeriodStart": _format_dt(current_period_start),
        "currentPeriodEnd": _format_dt(current_period_end),
        "quotaAnchorDay": quota_anchor_day,
        "scheduledTargetPriceId": scheduled_target_price_id,
        "scheduledEffectiveAt": _format_dt(scheduled_effective_at),
    }
    return _json_response(200, body)


def _legacy_redirect(session_id: str) -> Dict[str, Any]:
    stripe = _import_stripe()
    if not stripe or not settings.stripe_secret_key:
        logger.warning("Stripe SDK unavailable or secret not set")
        return _json_response(503, {"message": "Stripe is not available"})

    stripe.api_key = settings.stripe_secret_key
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        redirect_url = getattr(session, "url", None)
        if not redirect_url:
            raise ValueError("checkout session missing url")
        return {
            "statusCode": 302,
            "headers": {"Location": redirect_url},
        }
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to issue checkout redirect: %s", exc)
        return _json_response(404, {"message": "Checkout session not found"})


def _legacy_status_response(session_id: str) -> Dict[str, Any]:
    stripe = _import_stripe()
    if not stripe or not settings.stripe_secret_key:
        logger.warning("Stripe SDK unavailable or secret not set")
        return _json_response(503, {"message": "Stripe is not available"})

    stripe.api_key = settings.stripe_secret_key
    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve checkout session for status: %s", exc)
        return _json_response(404, {"message": "Checkout session not found"})

    metadata = getattr(session, "metadata", None) or {}
    group_id = metadata.get("group_id")
    subscription_obj = getattr(session, "subscription", None)
    if isinstance(subscription_obj, dict):
        subscription_status = subscription_obj.get("status")
    else:
        subscription_status = getattr(subscription_obj, "status", None)

    body = {
        "sessionId": session_id,
        "groupId": group_id,
        "subscriptionStatus": subscription_status,
        "proActive": subscription_status in {"active", "trialing"},
    }
    return _json_response(200, body)


def _create_checkout_session(*, stripe, group_id: str, price_id: str, customer_id: Optional[str]) -> Optional[str]:
    kwargs = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": "https://line.me/R/nv/chat",
        "cancel_url": "https://line.me/R/nv/chat",
        "metadata": {"group_id": group_id},
        "subscription_data": {"metadata": {"group_id": group_id}},
    }
    if customer_id:
        kwargs["customer"] = customer_id

    try:
        session = stripe.checkout.Session.create(**kwargs)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to create checkout session", exc_info=True)
        return None
    return getattr(session, "url", None)


def _create_upgrade_hosted_url(
    *,
    stripe,
    customer_id: Optional[str],
    subscription_id: str,
    item_id: Optional[str],
    target_price_id: str,
) -> Optional[str]:
    if not customer_id:
        return None

    return_url = "https://line.me/R/nv/chat"

    flow_data = {
        "type": "subscription_update_confirm",
        "subscription_update_confirm": {
            "subscription": subscription_id,
            "items": [
                {
                    "price": target_price_id,
                }
            ],
        },
    }
    if item_id:
        flow_data["subscription_update_confirm"]["items"][0]["id"] = item_id

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
            flow_data=flow_data,
        )
        return getattr(session, "url", None)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to create hosted upgrade confirmation flow", exc_info=True)
        return None


def _schedule_subscription_change(*, stripe, subscription: Dict[str, Any], subscription_id: str, target_price_id: str) -> Optional[datetime]:
    current_period_end = _to_datetime(subscription.get("current_period_end"))
    if not current_period_end:
        return None

    item_quantity = 1
    try:
        items_data = ((subscription.get("items") or {}).get("data") or [])
        if items_data:
            item_quantity = int(items_data[0].get("quantity") or 1)
    except Exception:  # pylint: disable=broad-except
        item_quantity = 1

    try:
        schedule = stripe.SubscriptionSchedule.create(from_subscription=subscription_id)
        current_start = _to_datetime(subscription.get("current_period_start"))
        if not current_start:
            current_start = datetime.now(timezone.utc)

        stripe.SubscriptionSchedule.modify(
            schedule.get("id"),
            end_behavior="release",
            phases=[
                {
                    "start_date": int(current_start.timestamp()),
                    "end_date": int(current_period_end.timestamp()),
                    "items": [{"price": _extract_subscription_item(subscription)[0], "quantity": item_quantity}],
                },
                {
                    "start_date": int(current_period_end.timestamp()),
                    "items": [{"price": target_price_id, "quantity": item_quantity}],
                },
            ],
        )
        return current_period_end
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to schedule subscription change", exc_info=True)
        return None


def _resolve_current_plan(
    repo: NeonMessageRepository,
    group_id: str,
    catalog,
    current_price_id: Optional[str],
) -> str:
    price_def = catalog.resolve_price(current_price_id)
    if price_def:
        return price_def.plan

    status, entitlement_plan, *_rest = repo.get_subscription_plan(group_id)
    if status in {"active", "trialing"} and entitlement_plan:
        return entitlement_plan
    return FREE_PLAN


def _target_plan_from_key(target_key: str) -> str:
    if target_key.startswith("standard"):
        return STANDARD_PLAN
    if target_key.startswith("pro"):
        return PRO_PLAN
    return FREE_PLAN


def _plan_rank(plan_key: str) -> int:
    if plan_key == FREE_PLAN:
        return 0
    if plan_key == STANDARD_PLAN:
        return 1
    return 2


def _extract_subscription_item(subscription: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    items = ((subscription.get("items") or {}).get("data") or [])
    if not items:
        return (None, None)
    first = items[0]
    price = first.get("price") if isinstance(first, dict) else None
    price_id = price.get("id") if isinstance(price, dict) else None
    item_id = first.get("id") if isinstance(first, dict) else None
    return (price_id, item_id)


def _extract_mode(event: Dict[str, Any]) -> str:
    params = event.get("queryStringParameters") or {}
    return ((params.get("mode") or "").strip().lower())


def _extract_session_id(event: Dict[str, Any]) -> Optional[str]:
    params = event.get("queryStringParameters") or {}
    return params.get("session_id") or params.get("sessionId")


def _extract_token(event: Dict[str, Any]) -> Optional[str]:
    params = event.get("queryStringParameters") or {}
    token = params.get("st")
    return (token or "").strip() or None


def _extract_target(event: Dict[str, Any]) -> Optional[str]:
    params = event.get("queryStringParameters") or {}
    return params.get("target")


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _format_dt(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    dt = value.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _get_repo() -> NeonMessageRepository:
    global _repo
    if _repo is None:
        client = get_client(settings.neon_database_url)
        _repo = NeonMessageRepository(
            client,
            max_group_languages=settings.max_group_languages,
            message_encryption_key=settings.message_encryption_key,
        )
    return _repo


def _import_stripe():
    try:
        import importlib

        return importlib.import_module("stripe")
    except ModuleNotFoundError:
        return None


def _json_response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
