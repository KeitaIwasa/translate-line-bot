from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from .config import get_settings
from .infra.neon_client import get_client
from .infra.neon_repositories import NeonMessageRepository

logger = logging.getLogger(__name__)
settings = get_settings()
_repo: Optional[NeonMessageRepository] = None


def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    """Stripe Checkout の短縮リンク用リダイレクト Lambda。"""

    session_id = _extract_session_id(event)
    if not session_id:
        return _json_response(400, {"message": "session_id is required"})

    # status モードで呼ばれた場合は購読状態だけ返す
    if _is_status_mode(event):
        return _status_response(session_id)

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


def _extract_session_id(event: Dict[str, Any]) -> Optional[str]:
    params = event.get("queryStringParameters") or {}
    return params.get("session_id") or params.get("sessionId")


def _is_status_mode(event: Dict[str, Any]) -> bool:
    params = event.get("queryStringParameters") or {}
    mode = params.get("mode") or params.get("format")
    return (mode or "").lower() == "status"


def _status_response(session_id: str) -> Dict[str, Any]:
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

    group_id, subscription_status = _extract_group_and_status(session)
    session_status = getattr(session, "status", None)

    body = {
        "sessionId": session_id,
        "sessionStatus": session_status,
        "groupId": group_id,
        "subscriptionStatus": subscription_status,
        "proActive": subscription_status in {"active", "trialing"},
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _extract_group_and_status(session: Any) -> Tuple[Optional[str], Optional[str]]:
    """セッションと DB から group_id と購読ステータスを推定する。"""

    group_id = None
    subscription_status = None
    subscription_id: Optional[str] = None

    metadata = getattr(session, "metadata", None) or {}
    group_id = metadata.get("group_id")

    subscription_obj = getattr(session, "subscription", None)
    if isinstance(subscription_obj, dict):
        subscription_status = subscription_obj.get("status")
        subscription_id = subscription_obj.get("id")
    else:
        subscription_id = subscription_obj if isinstance(subscription_obj, str) else None
        subscription_status = getattr(subscription_obj, "status", None)

    # DB 上のステータスを優先
    if group_id:
        subscription_status = _get_subscription_status_from_db(group_id) or subscription_status

    # DB で取れない場合は Stripe から補完
    if (not subscription_status) and subscription_id:
        try:
            stripe = _import_stripe()
            if stripe and settings.stripe_secret_key:
                stripe.api_key = settings.stripe_secret_key
                subscription = stripe.Subscription.retrieve(subscription_id)
                subscription_status = (subscription.get("status") if isinstance(subscription, dict) else None) or subscription_status
                group_id = group_id or (subscription.get("metadata", {}) if isinstance(subscription, dict) else {}).get("group_id")
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to retrieve subscription for status fallback", exc_info=True)

    return group_id, subscription_status


def _get_subscription_status_from_db(group_id: str) -> Optional[str]:
    global _repo
    try:
        if _repo is None:
            client = get_client(settings.neon_database_url)
            _repo = NeonMessageRepository(client, max_group_languages=settings.max_group_languages)
        return _repo.get_subscription_status(group_id)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to fetch subscription status from DB", exc_info=True, extra={"group_id": group_id})
        return None


def _import_stripe():
    try:
        import importlib

        return importlib.import_module("stripe")
    except ModuleNotFoundError:
        return None


def _json_response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }
