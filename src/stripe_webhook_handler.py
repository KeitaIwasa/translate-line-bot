from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import psycopg
import stripe
import requests

from src.config import get_settings
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.plan_policy import FREE_PLAN, PRO_PLAN
from src.infra.gemini_translation import GeminiTranslationAdapter
from src.infra.neon_client import get_client
from src.infra.neon_repositories import NeonMessageRepository
from src.infra.stripe_price_catalog import build_price_catalog
from src.presentation.multilingual_message import build_multilingual_message, dedup_lang_codes

logger = logging.getLogger(__name__)
stripe.default_http_client = stripe.http_client.RequestsClient()
settings = get_settings()
price_catalog = build_price_catalog(settings)
_repo: Optional[NeonMessageRepository] = None

PAYMENT_CONFIRMED_MESSAGE_EN = (
    "Payment has been confirmed. Translation service has resumed for this group. Thank you!\n"
    "If you want to change your plan, mention this official account with \"@\" and say \"Change plan\"."
)
OWNER_LEFT_RESERVATION_MESSAGE_EN = (
    "A new billing owner has reserved continuation for the next billing cycle.\n"
    "The paid plan will continue from the next renewal date with the newly registered card."
)


def lambda_handler(event: Dict[str, Any], _context: Any):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8")

    sig_header = _get_header(event.get("headers") or {}, "Stripe-Signature")
    webhook_secret = settings.stripe_webhook_secret
    stripe.api_key = settings.stripe_secret_key
    if not webhook_secret or not stripe.api_key:
        logger.error("Stripe secrets missing; cannot process webhook")
        return {"statusCode": 500, "body": json.dumps({"message": "Stripe secrets missing"})}

    try:
        stripe_event = stripe.Webhook.construct_event(body, sig_header, webhook_secret)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Stripe signature verification failed: %s", exc)
        return {"statusCode": 400, "body": json.dumps({"message": "invalid signature"})}

    event_type = stripe_event["type"]
    data_object = stripe_event["data"]["object"]
    logger.info("Stripe webhook received", extra={"type": event_type})

    handlers = {
        "invoice.payment_succeeded": _handle_payment_succeeded,
        "invoice.payment_failed": _handle_payment_failed,
        "customer.subscription.deleted": _handle_subscription_deleted,
        "customer.subscription.updated": _handle_subscription_updated,
        "checkout.session.completed": _handle_checkout_session_completed,
    }
    handler = handlers.get(event_type)
    if not handler:
        return {"statusCode": 200, "body": json.dumps({"message": "ignored"})}

    try:
        handler(data_object, stripe_event.get("created"))
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to handle Stripe event: %s", exc)
        return {"statusCode": 500, "body": json.dumps({"message": "internal error"})}

    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}


def _handle_payment_succeeded(invoice: Dict[str, Any], event_created: Any) -> None:
    subscription = _retrieve_subscription_from_invoice(invoice)
    if not subscription:
        return

    group_id = _extract_group_id(subscription, invoice)
    if not group_id:
        logger.warning("group_id not found in Stripe metadata", extra={"invoice_id": invoice.get("id")})
        return

    _sync_subscription(group_id, subscription, status_override=subscription.get("status"), event_created=event_created)
    _set_translation_enabled(group_id, True)
    _push_payment_confirmation(group_id)


def _handle_payment_failed(invoice: Dict[str, Any], event_created: Any) -> None:
    subscription = _retrieve_subscription_from_invoice(invoice)
    if not subscription:
        return

    group_id = _extract_group_id(subscription, invoice)
    if not group_id:
        logger.warning("group_id missing on payment_failed")
        return

    _sync_subscription(group_id, subscription, status_override="unpaid", event_created=event_created)
    _set_translation_enabled(group_id, False)


def _handle_subscription_deleted(subscription: Dict[str, Any], event_created: Any) -> None:
    group_id = _extract_group_id(subscription, subscription)
    if not group_id:
        logger.warning("group_id missing on subscription.deleted")
        return

    _sync_subscription(group_id, subscription, status_override="canceled", event_created=event_created)
    _set_translation_enabled(group_id, False)


def _handle_subscription_updated(subscription: Dict[str, Any], event_created: Any) -> None:
    group_id = _extract_group_id(subscription, subscription)
    if not group_id:
        logger.warning("group_id missing on subscription.updated")
        return

    status = subscription.get("status") or "active"
    _sync_subscription(group_id, subscription, status_override=status, event_created=event_created)
    _set_translation_enabled(group_id, status in {"active", "trialing"})


def _handle_checkout_session_completed(session: Dict[str, Any], event_created: Any) -> None:
    session_id = session.get("id")
    if not session_id:
        logger.warning("checkout.session.completed missing id")
        return

    try:
        full_session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve session %s: %s", session_id, exc)
        return

    metadata = full_session.get("metadata") or {}
    if (full_session.get("mode") == "setup") and isinstance(metadata, dict) and metadata.get("flow_type") == "renewal_setup":
        _handle_renewal_setup_completed(full_session)
        return

    subscription = full_session.get("subscription") or {}
    if not isinstance(subscription, dict):
        try:
            subscription = stripe.Subscription.retrieve(subscription)
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to retrieve subscription from checkout session", exc_info=True)
            return

    group_id = _extract_group_id(subscription, full_session)
    if not group_id:
        logger.warning("group_id missing on checkout.session.completed", extra={"session_id": session_id})
        return

    status = subscription.get("status") or "active"
    _sync_subscription(group_id, subscription, status_override=status, event_created=event_created)
    _set_translation_enabled(group_id, status in {"active", "trialing"})
    if status in {"active", "trialing"}:
        _push_payment_confirmation(group_id)


def _handle_renewal_setup_completed(session: Dict[str, Any]) -> None:
    group_id = _extract_group_id(session, session)
    line_user_id = _extract_line_user_id(session, session)
    if not group_id or not line_user_id:
        logger.warning("renewal setup session missing group or user")
        return

    repo = _get_repo()
    owner_lost, reserved_user_id, _reserved_customer_id, _reserved_schedule_id, _reserved_effective_at, _reserved_price_id, _reserved_plan, _reserved_interval = repo.get_renewal_reservation(group_id)
    if not owner_lost:
        return
    if reserved_user_id:
        return

    metadata = session.get("metadata") or {}
    target_price_id = str(metadata.get("renewal_price_id") or "").strip() if isinstance(metadata, dict) else ""
    if not target_price_id:
        logger.warning("renewal setup session missing renewal_price_id", extra={"group_id": group_id})
        return

    customer_id = str(session.get("customer") or "").strip()
    if not customer_id:
        logger.warning("renewal setup session missing customer", extra={"group_id": group_id})
        return

    (
        _status,
        _entitlement_plan,
        _billing_interval,
        _is_grandfathered,
        _stripe_price_id,
        _current_period_start,
        current_period_end,
        _quota_anchor_day,
        _scheduled_target_price_id,
        _scheduled_effective_at,
    ) = repo.get_subscription_plan(group_id)
    if not current_period_end:
        logger.warning("renewal setup missing current_period_end", extra={"group_id": group_id})
        return

    setup_intent = session.get("setup_intent")
    payment_method_id = None
    if setup_intent:
        try:
            if isinstance(setup_intent, dict):
                payment_method_id = setup_intent.get("payment_method")
            else:
                setup_intent_obj = stripe.SetupIntent.retrieve(setup_intent)
                payment_method_id = setup_intent_obj.get("payment_method")
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to retrieve setup intent for renewal", exc_info=True, extra={"group_id": group_id})

    if payment_method_id:
        try:
            stripe.Customer.modify(customer_id, invoice_settings={"default_payment_method": payment_method_id})
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to set default payment method on renewal customer", exc_info=True, extra={"group_id": group_id})

    price_def = price_catalog.resolve_price(target_price_id)
    renewal_plan = price_def.plan if price_def else PRO_PLAN
    renewal_interval = price_def.interval if price_def else "month"
    schedule = _create_renewal_schedule(
        customer_id=customer_id,
        line_user_id=line_user_id,
        group_id=group_id,
        target_price_id=target_price_id,
        current_period_end=current_period_end,
        default_payment_method_id=payment_method_id,
    )
    if not schedule:
        return

    created = repo.create_renewal_reservation(
        group_id=group_id,
        renewal_owner_user_id=line_user_id,
        renewal_stripe_customer_id=customer_id,
        renewal_subscription_schedule_id=str(schedule.get("id") or ""),
        renewal_effective_at=current_period_end,
        renewal_price_id=target_price_id,
        renewal_plan=renewal_plan,
        renewal_billing_interval=renewal_interval,
        renewal_setup_session_id=str(session.get("id") or ""),
    )
    if created:
        _push_message(group_id, _build_multilingual_message(OWNER_LEFT_RESERVATION_MESSAGE_EN, group_id))


def _retrieve_subscription_from_invoice(invoice: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    subscription_id = invoice.get("subscription")
    if not subscription_id and invoice.get("id"):
        try:
            fetched = stripe.Invoice.retrieve(invoice["id"])
            subscription_id = fetched.get("subscription")
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to retrieve invoice for missing subscription", exc_info=True)

    if not subscription_id:
        return None

    try:
        return stripe.Subscription.retrieve(subscription_id)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve subscription from invoice", exc_info=True)
        return None


def _sync_subscription(group_id: str, subscription: Dict[str, Any], *, status_override: Optional[str], event_created: Any) -> None:
    price_id = _extract_primary_price_id(subscription)
    price_def = price_catalog.resolve_price(price_id)

    status = status_override or subscription.get("status") or "active"
    period_start = _to_datetime(subscription.get("current_period_start"))
    period_end = _to_datetime(subscription.get("current_period_end"))
    subscription_id = str(subscription.get("id") or "")
    if subscription_id and (period_start is None or period_end is None):
        resolved_start, resolved_end = _retrieve_period_bounds(subscription_id)
        period_start = period_start or resolved_start
        period_end = period_end or resolved_end

    if price_def:
        entitlement_plan = price_def.plan
        billing_interval = price_def.interval
        is_grandfathered = bool(price_def.is_grandfathered)
    else:
        # 後方互換: 価格ID未登録でも、active/trialing は Pro とみなして継続
        entitlement_plan = PRO_PLAN if status in {"active", "trialing"} else FREE_PLAN
        billing_interval = "month"
        is_grandfathered = False

    quota_anchor_base = period_start or period_end
    quota_anchor_day = quota_anchor_base.day if quota_anchor_base else None

    customer_id = str(subscription.get("customer") or "")
    billing_owner_user_id = _extract_line_user_id(subscription, subscription)
    if (not billing_owner_user_id) and subscription_id:
        billing_owner_user_id = _confirm_pending_billing_owner_if_applicable(
            group_id=group_id,
            subscription_id=subscription_id,
            event_created=event_created,
        )
    if (not billing_owner_user_id) and _is_renewal_flow(subscription, subscription):
        _owner_lost, renewal_owner_user_id, _renewal_customer_id, _renewal_schedule_id, _renewal_effective_at, _renewal_price_id, _renewal_plan, _renewal_interval = _get_repo().get_renewal_reservation(group_id)
        billing_owner_user_id = renewal_owner_user_id

    _upsert_subscription(
        group_id=group_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        status=status,
        current_period_start=period_start,
        current_period_end=period_end,
        stripe_price_id=price_id,
        entitlement_plan=entitlement_plan,
        billing_interval=billing_interval,
        is_grandfathered=is_grandfathered,
        quota_anchor_day=quota_anchor_day,
        billing_owner_user_id=billing_owner_user_id,
    )
    if _is_renewal_flow(subscription, subscription) and status in {"active", "trialing"}:
        _get_repo().clear_renewal_reservation(group_id)


def _retrieve_period_bounds(subscription_id: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    try:
        fetched = stripe.Subscription.retrieve(subscription_id)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve subscription for period bounds", extra={"subscription_id": subscription_id}, exc_info=True)
        return (None, None)
    return (_to_datetime(fetched.get("current_period_start")), _to_datetime(fetched.get("current_period_end")))


def _confirm_pending_billing_owner_if_applicable(group_id: str, subscription_id: str, event_created: Any) -> Optional[str]:
    event_dt = _to_datetime(event_created)
    if not event_dt:
        return None

    repo = _get_repo()
    (
        owner_user_id,
        pending_user_id,
        pending_subscription_id,
        pending_expires_at,
        pending_created_at,
    ) = repo.get_billing_owner_claim_state(group_id)
    if owner_user_id or not pending_user_id or not pending_subscription_id or pending_subscription_id != subscription_id:
        return None
    if not pending_expires_at or not pending_created_at:
        return None
    if event_dt < pending_created_at or event_dt > pending_expires_at:
        return None

    repo.confirm_pending_billing_owner_claim(group_id, subscription_id, pending_user_id)
    return pending_user_id


def _extract_primary_price_id(subscription: Dict[str, Any]) -> Optional[str]:
    try:
        items = ((subscription.get("items") or {}).get("data") or [])
        if not items:
            return None
        first = items[0]
        price = first.get("price") if isinstance(first, dict) else None
        if isinstance(price, dict):
            return price.get("id")
        return None
    except Exception:  # pylint: disable=broad-except
        return None


def _extract_group_id(primary_obj: Dict[str, Any], fallback_obj: Dict[str, Any]) -> Optional[str]:
    meta = primary_obj.get("metadata") or {}
    group_id = meta.get("group_id") if isinstance(meta, dict) else None
    if group_id:
        return str(group_id)
    fallback_meta = fallback_obj.get("metadata") or {}
    if isinstance(fallback_meta, dict):
        fallback_group = fallback_meta.get("group_id")
        if fallback_group:
            return str(fallback_group)
    return None


def _extract_line_user_id(primary_obj: Dict[str, Any], fallback_obj: Dict[str, Any]) -> Optional[str]:
    meta = primary_obj.get("metadata") or {}
    user_id = meta.get("line_user_id") if isinstance(meta, dict) else None
    if user_id:
        return str(user_id)
    fallback_meta = fallback_obj.get("metadata") or {}
    if isinstance(fallback_meta, dict):
        fallback_user = fallback_meta.get("line_user_id")
        if fallback_user:
            return str(fallback_user)
    return None


def _is_renewal_flow(primary_obj: Dict[str, Any], fallback_obj: Dict[str, Any]) -> bool:
    meta = primary_obj.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("flow_type") == "renewal_setup":
        return True
    fallback_meta = fallback_obj.get("metadata") or {}
    return bool(isinstance(fallback_meta, dict) and fallback_meta.get("flow_type") == "renewal_setup")


def _create_renewal_schedule(
    *,
    customer_id: str,
    line_user_id: str,
    group_id: str,
    target_price_id: str,
    current_period_end: datetime,
    default_payment_method_id: Optional[str],
):
    try:
        kwargs = {
            "customer": customer_id,
            "start_date": int(current_period_end.timestamp()),
            "end_behavior": "release",
            "phases": [
                {
                    "items": [{"price": target_price_id, "quantity": 1}],
                    "metadata": {
                        "group_id": group_id,
                        "line_user_id": line_user_id,
                        "flow_type": "renewal_setup",
                    },
                }
            ],
        }
        if default_payment_method_id:
            kwargs["default_settings"] = {"default_payment_method": default_payment_method_id}
        return stripe.SubscriptionSchedule.create(**kwargs)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to create renewal schedule", exc_info=True, extra={"group_id": group_id})
        return None


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _get_repo() -> NeonMessageRepository:
    global _repo
    if _repo is None:
        _repo = NeonMessageRepository(get_client(settings.neon_database_url))
    return _repo


def _upsert_subscription(
    group_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    status: str,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
    *,
    stripe_price_id: Optional[str],
    entitlement_plan: str,
    billing_interval: str,
    is_grandfathered: bool,
    quota_anchor_day: Optional[int],
    billing_owner_user_id: Optional[str],
) -> None:
    repo = _get_repo()
    repo.upsert_subscription(
        group_id=group_id,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        stripe_price_id=stripe_price_id,
        entitlement_plan=entitlement_plan,
        billing_interval=billing_interval,
        is_grandfathered=is_grandfathered,
        quota_anchor_day=quota_anchor_day,
        billing_owner_user_id=billing_owner_user_id,
    )


def _set_translation_enabled(group_id: str, enabled: bool) -> None:
    dsn = settings.neon_database_url
    query_settings = """
        INSERT INTO group_settings (group_id, translation_enabled, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (group_id)
        DO UPDATE SET translation_enabled = EXCLUDED.translation_enabled, updated_at = NOW()
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(query_settings, (group_id, enabled))


def _push_payment_confirmation(group_id: str) -> None:
    text = _build_multilingual_message(PAYMENT_CONFIRMED_MESSAGE_EN, group_id)
    _push_message(group_id, text)


def _build_multilingual_message(base_text: str, group_id: str) -> str:
    languages = dedup_lang_codes(_fetch_group_languages(group_id))
    translator = _get_interface_translation_service()
    return build_multilingual_message(
        base_text=base_text,
        languages=languages,
        translator=translator,
        logger=logger,
        warning_log="Payment confirmation translation failed",
    )


def _fetch_group_languages(group_id: str) -> List[str]:
    query = "SELECT lang_code FROM group_languages WHERE group_id = %s ORDER BY lang_code"
    try:
        with psycopg.connect(settings.neon_database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (group_id,))
                rows = cur.fetchall()
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to fetch group languages", exc_info=True, extra={"group_id": group_id})
        return []

    return [str(row[0]).lower() for row in rows if row and row[0]]


@lru_cache(maxsize=1)
def _get_interface_translation_service() -> InterfaceTranslationService | None:
    api_key = settings.gemini_api_key
    if not api_key:
        logger.warning("GEMINI_API_KEY missing; skip interface translation")
        return None

    model = settings.gemini_model or "gemini-flash-latest"
    timeout_seconds = int(getattr(settings, "gemini_timeout_seconds", 8) or 8)
    adapter = GeminiTranslationAdapter(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
    return InterfaceTranslationService(adapter)


def _push_message(group_id: str, text: str) -> None:
    if not text:
        return

    token = settings.line_channel_access_token
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN missing; skip push message", extra={"group_id": group_id})
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": group_id,
        "messages": [{"type": "text", "text": text[:5000]}],
    }

    try:
        response = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers=headers,
            json=payload,
            timeout=8,
        )
        if response.status_code >= 400:
            logger.warning(
                "Failed to push payment confirmation",
                extra={"status_code": response.status_code, "body": response.text[:200]},
            )
    except Exception:  # pylint: disable=broad-except
        logger.warning("LINE push request failed", exc_info=True)


def _get_header(headers: Dict[str, Any], key: str) -> str:
    target = key.lower()
    for k, v in headers.items():
        if str(k).lower() == target:
            return str(v)
    return ""
