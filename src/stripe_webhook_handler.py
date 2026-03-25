from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

import psycopg
import stripe
import requests

from src.config import get_settings
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.plan_policy import FREE_PLAN, PRO_PLAN
from src.infra.gemini_translation import GeminiTranslationAdapter
from src.infra.stripe_price_catalog import build_price_catalog
from src.presentation.reply_formatter import strip_source_echo

logger = logging.getLogger(__name__)
stripe.default_http_client = stripe.http_client.RequestsClient()
settings = get_settings()
price_catalog = build_price_catalog(settings)

PAYMENT_CONFIRMED_MESSAGE_EN = (
    "Payment has been confirmed. Translation service has resumed for this group. Thank you!\n"
    "If you want to change your plan, mention this official account with \"@\" and say \"Change plan\"."
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
        handler(data_object)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to handle Stripe event: %s", exc)
        return {"statusCode": 500, "body": json.dumps({"message": "internal error"})}

    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}


def _handle_payment_succeeded(invoice: Dict[str, Any]) -> None:
    subscription = _retrieve_subscription_from_invoice(invoice)
    if not subscription:
        return

    group_id = _extract_group_id(subscription, invoice)
    if not group_id:
        logger.warning("group_id not found in Stripe metadata", extra={"invoice_id": invoice.get("id")})
        return

    _sync_subscription(group_id, subscription, status_override=subscription.get("status"))
    _set_translation_enabled(group_id, True)
    _push_payment_confirmation(group_id)


def _handle_payment_failed(invoice: Dict[str, Any]) -> None:
    subscription = _retrieve_subscription_from_invoice(invoice)
    if not subscription:
        return

    group_id = _extract_group_id(subscription, invoice)
    if not group_id:
        logger.warning("group_id missing on payment_failed")
        return

    _sync_subscription(group_id, subscription, status_override="unpaid")
    _set_translation_enabled(group_id, False)


def _handle_subscription_deleted(subscription: Dict[str, Any]) -> None:
    group_id = _extract_group_id(subscription, subscription)
    if not group_id:
        logger.warning("group_id missing on subscription.deleted")
        return

    _sync_subscription(group_id, subscription, status_override="canceled")
    _set_translation_enabled(group_id, False)


def _handle_subscription_updated(subscription: Dict[str, Any]) -> None:
    group_id = _extract_group_id(subscription, subscription)
    if not group_id:
        logger.warning("group_id missing on subscription.updated")
        return

    status = subscription.get("status") or "active"
    _sync_subscription(group_id, subscription, status_override=status)
    _set_translation_enabled(group_id, status in {"active", "trialing"})


def _handle_checkout_session_completed(session: Dict[str, Any]) -> None:
    session_id = session.get("id")
    if not session_id:
        logger.warning("checkout.session.completed missing id")
        return

    try:
        full_session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve session %s: %s", session_id, exc)
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
    _sync_subscription(group_id, subscription, status_override=status)
    _set_translation_enabled(group_id, status in {"active", "trialing"})
    if status in {"active", "trialing"}:
        _push_payment_confirmation(group_id)


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


def _sync_subscription(group_id: str, subscription: Dict[str, Any], *, status_override: Optional[str]) -> None:
    price_id = _extract_primary_price_id(subscription)
    price_def = price_catalog.resolve_price(price_id)

    status = status_override or subscription.get("status") or "active"
    period_start = _to_datetime(subscription.get("current_period_start"))
    period_end = _to_datetime(subscription.get("current_period_end"))

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
    subscription_id = str(subscription.get("id") or "")
    billing_owner_user_id = _extract_line_user_id(subscription, subscription)

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


def _to_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


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
    dsn = settings.neon_database_url
    query_subscription = """
        INSERT INTO group_subscriptions (
            group_id,
            stripe_customer_id,
            stripe_subscription_id,
            status,
            current_period_start,
            current_period_end,
            stripe_price_id,
            entitlement_plan,
            billing_interval,
            is_grandfathered,
            quota_anchor_day,
            billing_owner_user_id,
            scheduled_target_price_id,
            scheduled_effective_at,
            created_at,
            updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, NOW(), NOW())
        ON CONFLICT (group_id)
        DO UPDATE SET
            stripe_customer_id = EXCLUDED.stripe_customer_id,
            stripe_subscription_id = EXCLUDED.stripe_subscription_id,
            status = EXCLUDED.status,
            current_period_start = EXCLUDED.current_period_start,
            current_period_end = EXCLUDED.current_period_end,
            stripe_price_id = EXCLUDED.stripe_price_id,
            entitlement_plan = EXCLUDED.entitlement_plan,
            billing_interval = EXCLUDED.billing_interval,
            is_grandfathered = EXCLUDED.is_grandfathered,
            quota_anchor_day = EXCLUDED.quota_anchor_day,
            billing_owner_user_id = COALESCE(group_subscriptions.billing_owner_user_id, EXCLUDED.billing_owner_user_id),
            scheduled_target_price_id = EXCLUDED.scheduled_target_price_id,
            scheduled_effective_at = EXCLUDED.scheduled_effective_at,
            updated_at = NOW()
    """

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                query_subscription,
                (
                    group_id,
                    stripe_customer_id,
                    stripe_subscription_id,
                    status,
                    current_period_start,
                    current_period_end,
                    stripe_price_id,
                    entitlement_plan,
                    billing_interval,
                    is_grandfathered,
                    quota_anchor_day,
                    billing_owner_user_id,
                ),
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
    trimmed = (base_text or "").strip()
    if not trimmed:
        return ""

    languages = _dedup_lang_codes(_fetch_group_languages(group_id))
    translator = _get_interface_translation_service()

    if not languages or not translator:
        return trimmed

    target_langs = [lang for lang in languages if not lang.startswith("en")]

    text_by_lang = {}
    if target_langs:
        try:
            translations = translator.translate(trimmed, target_langs)
            for item in translations or []:
                lowered = (item.lang or "").lower()
                if not lowered or lowered in text_by_lang:
                    continue
                cleaned = strip_source_echo(trimmed, item.text)
                normalized = (cleaned or item.text or "").strip()
                if not normalized:
                    continue
                text_by_lang[lowered] = normalized
        except Exception:  # pylint: disable=broad-except
            logger.warning("Payment confirmation translation failed", exc_info=True)

    lines: List[str] = [trimmed]
    for lang in languages:
        if lang.startswith("en"):
            continue
        translated = text_by_lang.get(lang)
        if not translated or translated in lines:
            continue
        lines.append(translated)

    joined = "\n\n".join(line for line in lines if line)
    return joined.strip()[:5000]


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


def _dedup_lang_codes(languages: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for code in languages:
        lowered = (code or "").lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(lowered)
    return deduped


@lru_cache(maxsize=1)
def _get_interface_translation_service() -> InterfaceTranslationService | None:
    api_key = settings.gemini_api_key
    if not api_key:
        logger.warning("GEMINI_API_KEY missing; skip interface translation")
        return None

    model = settings.gemini_model or "gemini-2.5-flash"
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
