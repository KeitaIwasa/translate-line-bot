from __future__ import annotations

import importlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

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
from .infra.signed_token import TokenError, issue_token, verify_token
from .infra.stripe_price_catalog import build_price_catalog

logger = logging.getLogger(__name__)
settings = get_settings()
_repo: Optional[NeonMessageRepository] = None

CHECKOUT_SCOPE = "checkout"
CHECKOUT_SESSION_SCOPE = "checkout_session"
CHECKOUT_OAUTH_STATE_SCOPE = "checkout_oauth_state"
LINE_AUTHORIZE_URL = "https://access.line.me/oauth2/v2.1/authorize"
LINE_TOKEN_URL = "https://api.line.me/oauth2/v2.1/token"
LINE_PROFILE_URL = "https://api.line.me/v2/profile"
OWNER_ONLY_MESSAGE = "Only the billing owner can manage this subscription."
LOGIN_REQUIRED_MESSAGE = "LINE login is required."
NOT_GROUP_MEMBER_MESSAGE = "Only LINE group members can open this page."
DEFAULT_RETURN_PATH = "/pro.html"
ALLOWED_RETURN_PATHS = {"/pro.html", "/en/pro.html", "/zh-tw/pro.html", "/th/pro.html"}


def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    _warn_if_deprecated_api_base(event)
    mode = _extract_mode(event)

    if mode == "auth_start":
        return _handle_auth_start(event)
    if mode == "auth_callback":
        return _handle_auth_callback(event)
    if mode == "status":
        return _handle_status(event)
    if mode == "prepare":
        return _handle_prepare(event)
    if mode == "start":
        return _handle_start(event)
    if mode == "portal":
        return _handle_portal(event)

    session_id = _extract_session_id(event)
    if session_id:
        return _legacy_redirect(session_id)

    return _json_response(400, {"message": "mode is required"})


def _handle_auth_start(event: Dict[str, Any]) -> Dict[str, Any]:
    token = _extract_subscription_token(event)
    if not token:
        return _json_response(400, {"message": "st is required"})

    payload = _verify_subscription_token(token)
    if payload is None:
        return _json_response(401, {"message": "invalid token"})

    if not _line_login_ready():
        return _json_response(503, {"message": "LINE Login is not available"})

    state = issue_token(
        {
            "scope": CHECKOUT_OAUTH_STATE_SCOPE,
            "st": token,
            "nonce": secrets.token_urlsafe(16),
            "return_to": _extract_return_to(event),
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
        },
        secret=_checkout_session_secret(),
    )
    url = (
        f"{LINE_AUTHORIZE_URL}?{urlencode({
            'response_type': 'code',
            'client_id': settings.line_login_channel_id,
            'redirect_uri': settings.line_login_redirect_uri,
            'state': state,
            'scope': 'profile openid',
        })}"
    )
    return _redirect_response(url)


def _handle_auth_callback(event: Dict[str, Any]) -> Dict[str, Any]:
    code = _extract_query_param(event, "code")
    state = _extract_query_param(event, "state")
    if not code or not state:
        return _json_response(400, {"message": "code and state are required"})

    try:
        state_payload = verify_token(state, secret=_checkout_session_secret(), scope=CHECKOUT_OAUTH_STATE_SCOPE)
    except TokenError:
        return _json_response(401, {"message": "invalid state"})

    token = str(state_payload.get("st") or "").strip()
    subscription_payload = _verify_subscription_token(token)
    if subscription_payload is None:
        return _json_response(401, {"message": "invalid token"})

    group_id = str(subscription_payload.get("group_id") or "").strip()
    if not group_id:
        return _json_response(400, {"message": "token missing group_id"})

    try:
        access_token = _exchange_line_login_code(code)
        line_user_id = _fetch_line_user_id(access_token)
    except (RuntimeError, HTTPError, URLError) as exc:
        logger.warning("LINE Login callback failed: %s", exc)
        return _json_response(401, {"message": "line login failed"})

    repo = _get_repo()
    if not repo.is_group_member(group_id, line_user_id):
        return _redirect_response(
            _build_frontend_url(
                token=token,
                checkout_session=None,
                return_to=_sanitize_return_to(state_payload.get("return_to")),
                error="not_member",
            )
        )

    checkout_session = issue_token(
        {
            "scope": CHECKOUT_SESSION_SCOPE,
            "group_id": group_id,
            "line_user_id": line_user_id,
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=12)).timestamp()),
        },
        secret=_checkout_session_secret(),
    )
    return _redirect_response(
        _build_frontend_url(
            token=token,
            checkout_session=checkout_session,
            return_to=_sanitize_return_to(state_payload.get("return_to")),
        )
    )


def _handle_status(event: Dict[str, Any]) -> Dict[str, Any]:
    auth = _authorize_member(event)
    if auth.error_response:
        return auth.error_response

    return _status_from_db(
        auth.group_id,
        auth.line_user_id,
        auth.owner_user_id,
    )


def _handle_start(event: Dict[str, Any]) -> Dict[str, Any]:
    target_raw = _extract_target(event)
    target = parse_target_price_key(target_raw)
    if not target:
        return _json_response(400, {"message": "target is invalid"})

    auth = _authorize_member(event)
    if auth.error_response:
        return auth.error_response
    if auth.owner_forbidden:
        return _json_response(403, {"message": OWNER_ONLY_MESSAGE, "reason": "owner_only"})

    stripe = _import_stripe()
    if not stripe or not settings.stripe_secret_key:
        logger.warning("Stripe SDK unavailable or secret not set")
        return _json_response(503, {"message": "Stripe is not available"})

    catalog = build_price_catalog(settings)
    target_price_id = catalog.resolve_target(target)
    if not target_price_id:
        return _json_response(400, {"message": "target price is not configured"})

    stripe.api_key = settings.stripe_secret_key
    repo = auth.repo
    customer_id, subscription_id, status = repo.get_subscription_detail(auth.group_id)
    is_active = status in {"active", "trialing"}

    if (not customer_id) or (not subscription_id) or (not is_active):
        session_url = _create_checkout_session(
            stripe=stripe,
            group_id=auth.group_id,
            price_id=target_price_id,
            customer_id=customer_id,
            line_user_id=auth.line_user_id,
        )
        if not session_url:
            return _json_response(500, {"message": "failed to create checkout session"})
        return _json_response(200, {"mode": "start", "result": "checkout_created", "redirectUrl": session_url})

    try:
        subscription = stripe.Subscription.retrieve(subscription_id, expand=["items.data.price"])
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve subscription for change: %s", exc)
        return _json_response(404, {"message": "subscription not found"})

    current_price_id, current_item_id = _extract_subscription_item(subscription)
    current_plan = _resolve_current_plan(repo, auth.group_id, catalog, current_price_id)
    target_plan = _target_plan_from_key(target)

    if current_price_id == target_price_id:
        return _json_response(200, {"mode": "start", "result": "already_current"})

    _claim_billing_owner_if_needed(auth)

    hosted_url = _create_subscription_update_hosted_url(
        stripe=stripe,
        customer_id=customer_id,
        subscription_id=subscription_id,
        item_id=current_item_id,
        target_price_id=target_price_id,
    )
    if not hosted_url:
        message = "failed to create upgrade checkout session" if _plan_rank(target_plan) > _plan_rank(current_plan) else "failed to create downgrade checkout session"
        return _json_response(500, {"message": message})
    return _json_response(200, {"mode": "start", "result": "checkout_created", "redirectUrl": hosted_url})


def _handle_prepare(event: Dict[str, Any]) -> Dict[str, Any]:
    target_raw = _extract_target(event)
    target = parse_target_price_key(target_raw)
    if not target:
        return _json_response(400, {"message": "target is invalid"})

    auth = _authorize_member(event)
    if auth.error_response:
        return auth.error_response
    if auth.owner_forbidden:
        return _json_response(403, {"message": OWNER_ONLY_MESSAGE, "reason": "owner_only"})

    stripe = _import_stripe()
    if not stripe or not settings.stripe_secret_key:
        logger.warning("Stripe SDK unavailable or secret not set")
        return _json_response(503, {"message": "Stripe is not available"})

    catalog = build_price_catalog(settings)
    target_price_id = catalog.resolve_target(target)
    if not target_price_id:
        return _json_response(400, {"message": "target price is not configured"})

    stripe.api_key = settings.stripe_secret_key
    repo = auth.repo
    customer_id, subscription_id, status = repo.get_subscription_detail(auth.group_id)
    is_active = status in {"active", "trialing"}

    if (not customer_id) or (not subscription_id) or (not is_active):
        session_url = _create_checkout_session(
            stripe=stripe,
            group_id=auth.group_id,
            price_id=target_price_id,
            customer_id=customer_id,
            line_user_id=auth.line_user_id,
        )
        if not session_url:
            return _json_response(500, {"message": "failed to create checkout session"})
        return _json_response(200, {"mode": "prepare", "result": "checkout_created", "redirectUrl": session_url})

    try:
        subscription = stripe.Subscription.retrieve(subscription_id, expand=["items.data.price"])
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to retrieve subscription for prepare: %s", exc)
        return _json_response(404, {"message": "subscription not found"})

    current_price_id, current_item_id = _extract_subscription_item(subscription)
    if current_price_id == target_price_id:
        return _json_response(200, {"mode": "prepare", "result": "already_current"})

    hosted_url = _create_subscription_update_hosted_url(
        stripe=stripe,
        customer_id=customer_id,
        subscription_id=subscription_id,
        item_id=current_item_id,
        target_price_id=target_price_id,
    )
    if not hosted_url:
        return _json_response(500, {"message": "failed to create checkout session"})
    return _json_response(200, {"mode": "prepare", "result": "checkout_created", "redirectUrl": hosted_url})


def _handle_portal(event: Dict[str, Any]) -> Dict[str, Any]:
    auth = _authorize_member(event)
    if auth.error_response:
        return auth.error_response
    if auth.owner_forbidden:
        return _json_response(403, {"message": OWNER_ONLY_MESSAGE, "reason": "owner_only"})

    stripe = _import_stripe()
    if not stripe or not settings.stripe_secret_key:
        logger.warning("Stripe SDK unavailable or secret not set")
        return _json_response(503, {"message": "Stripe is not available"})

    customer_id, _subscription_id, _status = auth.repo.get_subscription_detail(auth.group_id)
    if not customer_id:
        return _json_response(404, {"message": "customer not found"})

    _claim_billing_owner_if_needed(auth)

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


class _CheckoutAuth:
    def __init__(
        self,
        *,
        repo: Optional[NeonMessageRepository] = None,
        group_id: str = "",
        line_user_id: str = "",
        owner_user_id: Optional[str] = None,
        owner_forbidden: bool = False,
        error_response: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.repo = repo
        self.group_id = group_id
        self.line_user_id = line_user_id
        self.owner_user_id = owner_user_id
        self.owner_forbidden = owner_forbidden
        self.error_response = error_response


def _authorize_member(event: Dict[str, Any]) -> _CheckoutAuth:
    token = _extract_subscription_token(event)
    if not token:
        return _CheckoutAuth(error_response=_json_response(400, {"message": "st is required"}))

    subscription_payload = _verify_subscription_token(token)
    if subscription_payload is None:
        return _CheckoutAuth(error_response=_json_response(401, {"message": "invalid token"}))

    session_token = _extract_checkout_session_token(event)
    if not session_token:
        return _CheckoutAuth(error_response=_json_response(401, {"message": LOGIN_REQUIRED_MESSAGE, "reason": "login_required"}))

    try:
        session_payload = verify_token(session_token, secret=_checkout_session_secret(), scope=CHECKOUT_SESSION_SCOPE)
    except TokenError:
        return _CheckoutAuth(error_response=_json_response(401, {"message": LOGIN_REQUIRED_MESSAGE, "reason": "login_required"}))

    group_id = str(subscription_payload.get("group_id") or "").strip()
    session_group_id = str(session_payload.get("group_id") or "").strip()
    line_user_id = str(session_payload.get("line_user_id") or "").strip()
    if not group_id or group_id != session_group_id:
        return _CheckoutAuth(error_response=_json_response(401, {"message": "token mismatch"}))

    repo = _get_repo()
    if not repo.is_group_member(group_id, line_user_id):
        return _CheckoutAuth(error_response=_json_response(403, {"message": NOT_GROUP_MEMBER_MESSAGE, "reason": "not_member"}))

    owner_user_id = repo.get_billing_owner_user_id(group_id)
    return _CheckoutAuth(
        repo=repo,
        group_id=group_id,
        line_user_id=line_user_id,
        owner_user_id=owner_user_id,
        owner_forbidden=bool(owner_user_id and owner_user_id != line_user_id),
    )


def _claim_billing_owner_if_needed(auth: _CheckoutAuth) -> None:
    if auth.owner_user_id or not auth.repo:
        return
    auth.repo.set_billing_owner_user_id(auth.group_id, auth.line_user_id)
    auth.owner_user_id = auth.line_user_id


def _status_from_db(group_id: str, line_user_id: str, owner_user_id: Optional[str]) -> Dict[str, Any]:
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
        "billingOwnerAssigned": bool(owner_user_id),
        "isBillingOwner": bool(owner_user_id and owner_user_id == line_user_id),
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
        return {"statusCode": 302, "headers": {"Location": redirect_url}}
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


def _create_checkout_session(
    *,
    stripe,
    group_id: str,
    price_id: str,
    customer_id: Optional[str],
    line_user_id: str,
) -> Optional[str]:
    kwargs = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": "https://line.me/R/nv/chat",
        "cancel_url": "https://line.me/R/nv/chat",
        "metadata": {"group_id": group_id, "line_user_id": line_user_id},
        "subscription_data": {"metadata": {"group_id": group_id, "line_user_id": line_user_id}},
        "client_reference_id": line_user_id,
    }
    if customer_id:
        kwargs["customer"] = customer_id

    try:
        session = stripe.checkout.Session.create(**kwargs)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to create checkout session", exc_info=True)
        return None
    return getattr(session, "url", None)


def _create_subscription_update_hosted_url(
    *,
    stripe,
    customer_id: Optional[str],
    subscription_id: str,
    item_id: Optional[str],
    target_price_id: str,
) -> Optional[str]:
    if not customer_id:
        return None

    flow_data = {
        "type": "subscription_update_confirm",
        "subscription_update_confirm": {
            "subscription": subscription_id,
            "items": [{"price": target_price_id}],
        },
    }
    if item_id:
        flow_data["subscription_update_confirm"]["items"][0]["id"] = item_id

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url="https://line.me/R/nv/chat",
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


def _resolve_current_plan(repo: NeonMessageRepository, group_id: str, catalog, current_price_id: Optional[str]) -> str:
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


def _line_login_ready() -> bool:
    return all(
        [
            settings.line_login_channel_id,
            settings.line_login_channel_secret,
            settings.line_login_redirect_uri,
            _checkout_session_secret(),
        ]
    )


def _exchange_line_login_code(code: str) -> str:
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.line_login_redirect_uri,
            "client_id": settings.line_login_channel_id,
            "client_secret": settings.line_login_channel_secret,
        }
    ).encode("utf-8")
    req = Request(LINE_TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urlopen(req, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("LINE access token missing")
    return access_token


def _fetch_line_user_id(access_token: str) -> str:
    req = Request(
        LINE_PROFILE_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urlopen(req, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    user_id = str(data.get("userId") or "").strip()
    if not user_id:
        raise RuntimeError("LINE user id missing")
    return user_id


def _extract_mode(event: Dict[str, Any]) -> str:
    return (_query_params(event).get("mode") or "").strip().lower()


def _extract_session_id(event: Dict[str, Any]) -> Optional[str]:
    params = _query_params(event)
    return params.get("session_id") or params.get("sessionId")


def _extract_subscription_token(event: Dict[str, Any]) -> Optional[str]:
    token = _query_params(event).get("st")
    return (token or "").strip() or None


def _extract_checkout_session_token(event: Dict[str, Any]) -> Optional[str]:
    token = _query_params(event).get("cs")
    return (token or "").strip() or None


def _extract_target(event: Dict[str, Any]) -> Optional[str]:
    return _query_params(event).get("target")


def _extract_return_to(event: Dict[str, Any]) -> str:
    return _sanitize_return_to(_query_params(event).get("return_to"))


def _extract_query_param(event: Dict[str, Any], key: str) -> Optional[str]:
    value = _query_params(event).get(key)
    return (value or "").strip() or None


def _query_params(event: Dict[str, Any]) -> Dict[str, Any]:
    return event.get("queryStringParameters") or {}


def _sanitize_return_to(value: Any) -> str:
    text = str(value or "").strip()
    if text in ALLOWED_RETURN_PATHS:
        return text
    return DEFAULT_RETURN_PATH


def _verify_subscription_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        return verify_token(token, secret=settings.subscription_token_secret, scope=CHECKOUT_SCOPE)
    except TokenError:
        return None


def _checkout_session_secret() -> str:
    return settings.checkout_session_secret or settings.subscription_token_secret


def _build_frontend_url(
    *,
    token: str,
    checkout_session: Optional[str],
    return_to: str,
    error: Optional[str] = None,
) -> str:
    base_url = settings.subscription_frontend_base_url.rstrip("/")
    path = _sanitize_return_to(return_to)
    if base_url:
        url = f"{base_url}{path}"
    else:
        url = path

    params = [("st", token)]
    if checkout_session:
        params.append(("cs", checkout_session))
    if error:
        params.append(("error", error))
    return f"{url}?{urlencode(params)}"


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
        return importlib.import_module("stripe")
    except ModuleNotFoundError:
        return None


def _redirect_response(location: str) -> Dict[str, Any]:
    return {
        "statusCode": 302,
        "headers": {
            "Location": location,
        },
        "body": "",
    }


def _json_response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _warn_if_deprecated_api_base(event: Dict[str, Any]) -> None:
    params = _query_params(event)
    value = (params.get("api_base") or params.get("apiBase") or "").strip()
    if not value:
        return
    logger.warning("Deprecated query parameter ignored: api_base")
