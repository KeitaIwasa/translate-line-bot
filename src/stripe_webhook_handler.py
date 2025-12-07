from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List

import psycopg
import stripe
import requests

from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.infra.gemini_translation import GeminiTranslationAdapter
from src.presentation.reply_formatter import strip_source_echo

logger = logging.getLogger(__name__)
stripe.default_http_client = stripe.http_client.RequestsClient()

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
    _push_payment_confirmation(group_id)


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
        _push_payment_confirmation(group_id)


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


def _push_payment_confirmation(group_id: str) -> None:
    """支払い確認後のメッセージを設定言語すべてで通知する。"""

    text = _build_multilingual_message(PAYMENT_CONFIRMED_MESSAGE_EN, group_id)
    _push_message(group_id, text)


def _build_multilingual_message(base_text: str, group_id: str) -> str:
    """ベース文を設定言語へ翻訳して列挙する。"""

    trimmed = (base_text or "").strip()
    if not trimmed:
        return ""

    languages = _dedup_lang_codes(_fetch_group_languages(group_id))
    translator = _get_interface_translation_service()

    if not languages or not translator:
        return trimmed

    # 英語はベース文で代用するため除外して翻訳を行う
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
    """グループの設定言語をDBから取得する。"""

    dsn = os.getenv("NEON_DATABASE_URL")
    if not dsn:
        logger.warning("NEON_DATABASE_URL missing; skip fetching group languages", extra={"group_id": group_id})
        return []

    query = "SELECT lang_code FROM group_languages WHERE group_id = %s ORDER BY lang_code"
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (group_id,))
                rows = cur.fetchall()
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to fetch group languages", exc_info=True, extra={"group_id": group_id})
        return []

    return [str(row[0]).lower() for row in rows if row and row[0]]


def _dedup_lang_codes(languages: List[str]) -> List[str]:
    """言語コードの重複と空を取り除く。"""

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
    """インターフェース文言用の翻訳サービスを生成（シングルトン）。"""

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY missing; skip interface translation")
        return None

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    timeout = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "10"))
    adapter = GeminiTranslationAdapter(api_key=api_key, model=model, timeout_seconds=timeout)
    return InterfaceTranslationService(adapter)


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
