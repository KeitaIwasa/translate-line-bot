from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    """Stripe Checkout の短縮リンク用リダイレクト Lambda。"""

    session_id = _extract_session_id(event)
    if not session_id:
        return _json_response(400, {"message": "session_id is required"})

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
