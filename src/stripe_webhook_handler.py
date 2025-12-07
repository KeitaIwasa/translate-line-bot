from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import psycopg
import stripe
import requests

logger = logging.getLogger(__name__)
stripe.default_http_client = stripe.http_client.RequestsClient()


def lambda_handler(event: Dict[str, Any], _context: Any):
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8")

    sig_header = _get_header(event.get("headers") or {}, "Stripe-Signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
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
        "customer.subscription.deleted": _handle_subscription_deleted,
        "invoice.payment_failed": _handle_payment_failed,
        "checkout.session.completed": _handle_checkout_session_completed,
    }
    handler = handlers.get(event_type)
    if not handler:
        return {"statusCode": 200, "body": json.dumps({"message": "ignored"})}

    try:
        handler(data_object)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to handle Stripe event: %s", exc)
        return {"statusCode": 500, "body": json.dumps({"message": "internal error"})}

    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}


def _handle_payment_succeeded(invoice: Dict[str, Any]) -> None:
    subscription_id = invoice.get("subscription")
    customer_id = invoice.get("customer")
    if not subscription_id or not customer_id:
        logger.warning("invoice missing subscription/customer id", extra={"invoice_id": invoice.get("id")})
        if invoice.get("id"):
            # retry by retrieving invoice for completeness
            fetched = stripe.Invoice.retrieve(invoice["id"])
            subscription_id = subscription_id or fetched.get("subscription")
            customer_id = customer_id or fetched.get("customer")
        if not subscription_id or not customer_id:
            return

    subscription = stripe.Subscription.retrieve(subscription_id)
    group_id = _extract_group_id(subscription, invoice)
    if not group_id:
        logger.warning("group_id not found in Stripe metadata", extra={"subscription": subscription_id})
        return

    period_end_ts = subscription.get("current_period_end")
    period_end = (
        datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if isinstance(period_end_ts, (int, float)) else None
    )
    _upsert_subscription(
        group_id=group_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        status=subscription.get("status", "active"),
        current_period_end=period_end,
        enable_translation=True,
    )
    _push_message(group_id, "Payment has been confirmed. Your translation service has been resumed. Thank you very much.\nIf you would like to change your plan, please mention this official account with “@” and say “Change plan.")


def _handle_subscription_deleted(subscription: Dict[str, Any]) -> None:
    group_id = _extract_group_id(subscription, subscription)
    if not group_id:
        logger.warning("group_id missing on subscription.deleted")
        return
    period_end_ts = subscription.get("current_period_end")
    period_end = (
        datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if isinstance(period_end_ts, (int, float)) else None
    )
    _upsert_subscription(
        group_id=group_id,
        stripe_customer_id=subscription.get("customer", ""),
        stripe_subscription_id=subscription.get("id", ""),
        status="canceled",
        current_period_end=period_end,
        enable_translation=False,
    )


def _handle_payment_failed(invoice: Dict[str, Any]) -> None:
    subscription_id = invoice.get("subscription")
    subscription = stripe.Subscription.retrieve(subscription_id) if subscription_id else {}
    group_id = _extract_group_id(subscription, invoice)
    if not group_id:
        logger.warning("group_id missing on payment_failed")
        return
    period_end_ts = subscription.get("current_period_end")
    period_end = (
        datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if isinstance(period_end_ts, (int, float)) else None
    )
    _upsert_subscription(
        group_id=group_id,
        stripe_customer_id=invoice.get("customer", ""),
        stripe_subscription_id=subscription.get("id", subscription_id or ""),
        status="unpaid",
        current_period_end=period_end,
        enable_translation=False,
    )


def _handle_checkout_session_completed(session: Dict[str, Any]) -> None:
    """Handle checkout.session.completed to catch cases where invoice events lack ids."""
    session_id = session.get("id")
    try:
        full_session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["subscription"],
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve session %s: %s", session_id, exc)
        return

    subscription = full_session.get("subscription") or {}
    subscription_id = subscription.get("id") if isinstance(subscription, dict) else subscription
    customer_id = full_session.get("customer")
    if not subscription_id or not customer_id:
        logger.warning("session missing subscription/customer", extra={"session_id": session_id})
        return

    group_id = _extract_group_id(subscription if isinstance(subscription, dict) else {}, full_session)
    if not group_id:
        logger.warning("group_id missing on checkout.session.completed", extra={"session_id": session_id})
        return

    period_end_ts = subscription.get("current_period_end") if isinstance(subscription, dict) else None
    period_end = (
        datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if isinstance(period_end_ts, (int, float)) else None
    )
    status = subscription.get("status") if isinstance(subscription, dict) else "active"
    _upsert_subscription(
        group_id=group_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        status=status or "active",
        current_period_end=period_end,
        enable_translation=status in {"active", "trialing"},
    )
    if status in {"active", "trialing"}:
        _push_message(group_id, "Payment completed. Translation has been re-enabled for this group.")


def _extract_group_id(primary_obj: Dict[str, Any], fallback_obj: Dict[str, Any]) -> str | None:
    meta = primary_obj.get("metadata") or {}
    group_id = meta.get("group_id") if isinstance(meta, dict) else None
    if group_id:
        return group_id
    fallback_meta = fallback_obj.get("metadata") or {}
    return fallback_meta.get("group_id") if isinstance(fallback_meta, dict) else None


def _upsert_subscription(
    group_id: str,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    status: str,
    current_period_end: datetime | None,
    *,
    enable_translation: bool,
) -> None:
    dsn = os.getenv("NEON_DATABASE_URL")
    if not dsn:
        raise RuntimeError("NEON_DATABASE_URL is not set")

    query_subscription = """
        INSERT INTO group_subscriptions (
            group_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (group_id)
        DO UPDATE SET
            stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status,
            current_period_end = EXCLUDED.current_period_end,
            updated_at = NOW()
    """
    query_settings = """
        INSERT INTO group_settings (group_id, translation_enabled, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (group_id)
        DO UPDATE SET translation_enabled = EXCLUDED.translation_enabled, updated_at = NOW()
    """

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                query_subscription,
                (group_id, stripe_customer_id, stripe_subscription_id, status, current_period_end),
            )
            cur.execute(query_settings, (group_id, enable_translation))


def _push_message(group_id: str, text: str) -> None:
    access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not access_token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN missing; skip push")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    payload = {"to": group_id, "messages": [{"type": "text", "text": text[:5000]}]}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=5)
        if resp.status_code >= 300:
            logger.warning("LINE push failed", extra={"status": resp.status_code, "body": resp.text})
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("LINE push exception: %s", exc)


def _get_header(headers: Dict[str, Any], key: str) -> str:
    for candidate in (key, key.lower(), key.replace("-", "_"), key.replace("-", "_").lower()):
        value = headers.get(candidate)
        if value:
            return value
    return ""
