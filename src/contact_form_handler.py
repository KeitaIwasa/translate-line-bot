from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any, Dict, Optional

from .config import get_settings
from .infra.neon_client import get_client
from .infra.neon_repositories import NeonMessageRepository

logger = logging.getLogger(__name__)
settings = get_settings()
_repo: Optional[NeonMessageRepository] = None

MIN_MESSAGE_LENGTH = 5
MAX_MESSAGE_LENGTH = 2000
ALLOWED_LOCALES = {"ja", "en", "zh-TW", "th"}
DEFAULT_ALLOWED_ORIGIN = "https://kotori-ai.com"
OPTIONS_HEADERS = "Content-Type"


def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    method = (event.get("requestContext", {}).get("http", {}).get("method") or "").upper()
    origin = _extract_origin(event)

    if method == "OPTIONS":
        return _empty_response(204, origin)
    if method != "POST":
        return _json_response(405, {"message": "Method Not Allowed"}, origin)

    payload = _parse_body(event)
    if payload is None:
        return _json_response(400, {"message": "Invalid request"}, origin)

    email = _normalize_email(payload.get("email"))
    message = _normalize_message(payload.get("message"))
    locale = _normalize_locale(payload.get("locale"))
    honeypot = str(payload.get("website") or "").strip()

    if not email or not message:
        return _json_response(400, {"message": "Invalid request"}, origin)

    # Honeypot を踏んだ送信は成功扱いで破棄し、ボットの挙動を隠す。
    if honeypot:
        return _json_response(200, {"ok": True}, origin)

    try:
        _enforce_rate_limit(event)
    except TooManyRequestsError:
        return _json_response(429, {"message": "Too many requests"}, origin)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to evaluate contact rate limit")
        return _json_response(500, {"message": "Internal Server Error"}, origin)

    try:
        _send_contact_email(email=email, message=message, locale=locale, event=event)
        return _json_response(200, {"ok": True}, origin)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to send contact email")
        return _json_response(500, {"message": "Internal Server Error"}, origin)


def _parse_body(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    body = event.get("body")
    if not body:
        return None
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_email(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 254:
        return None
    _, address = parseaddr(candidate)
    if not address or "@" not in address:
        return None
    local, _, domain = address.rpartition("@")
    if not local or "." not in domain:
        return None
    return address


def _normalize_message(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    message = value.strip()
    if len(message) < MIN_MESSAGE_LENGTH or len(message) > MAX_MESSAGE_LENGTH:
        return None
    return message


def _normalize_locale(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip()
    return normalized if normalized in ALLOWED_LOCALES else "unknown"


def _enforce_rate_limit(event: Dict[str, Any]) -> None:
    max_count = max(settings.contact_rate_limit_max, 1)
    window_seconds = max(settings.contact_rate_limit_window_seconds, 60)
    client_ip = _extract_client_ip(event)
    if not client_ip:
        logger.warning("Client IP not found; skip contact rate limit")
        return

    if not settings.contact_ip_hash_salt:
        logger.warning("CONTACT_IP_HASH_SALT is empty; using weaker hash entropy")

    ip_hash = _hash_ip(client_ip, settings.contact_ip_hash_salt)
    now = datetime.now(timezone.utc)
    window_start = _floor_window(now, window_seconds)
    prune_before = now - timedelta(hours=24)
    count = _get_repo().increment_contact_rate_limit(
        ip_hash=ip_hash,
        window_start=window_start,
        prune_before=prune_before,
    )
    if count > max_count:
        raise TooManyRequestsError()


def _extract_client_ip(event: Dict[str, Any]) -> str:
    headers = event.get("headers") or {}
    xff = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For")
    if isinstance(xff, str) and xff.strip():
        return xff.split(",")[0].strip()
    return (
        event.get("requestContext", {})
        .get("http", {})
        .get("sourceIp", "")
        .strip()
    )


def _hash_ip(ip: str, salt: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"{ip}:{salt}".encode("utf-8"))
    return digest.hexdigest()


def _floor_window(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.timestamp())
    floored = epoch - (epoch % window_seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def _send_contact_email(*, email: str, message: str, locale: str, event: Dict[str, Any]) -> None:
    ses = _get_ses_client()
    sent_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    client_ip = _extract_client_ip(event) or "unknown"
    user_agent = _extract_user_agent(event)
    safe_message = _strip_control_characters(message)

    subject = f"[KOTORI Contact][{locale}] {sent_at}"
    text_body = (
        f"Email: {email}\n"
        f"Locale: {locale}\n"
        f"SentAt: {sent_at}\n"
        f"IP: {client_ip}\n"
        f"User-Agent: {user_agent}\n\n"
        f"Message:\n{safe_message}\n"
    )

    ses.send_email(
        FromEmailAddress=settings.contact_from_email,
        Destination={"ToAddresses": [settings.contact_to_email]},
        ReplyToAddresses=[email],
        Content={
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": text_body, "Charset": "UTF-8"}},
            }
        },
    )


def _strip_control_characters(value: str) -> str:
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", value)


def _extract_user_agent(event: Dict[str, Any]) -> str:
    headers = event.get("headers") or {}
    return (
        (headers.get("user-agent") or headers.get("User-Agent") or "").strip()
        or "unknown"
    )


def _extract_origin(event: Dict[str, Any]) -> str:
    headers = event.get("headers") or {}
    raw_origin = headers.get("origin") or headers.get("Origin") or ""
    allowed = _allowed_origins()
    if raw_origin in allowed:
        return raw_origin
    return next(iter(allowed), DEFAULT_ALLOWED_ORIGIN)


def _allowed_origins() -> set[str]:
    raw = settings.contact_allowed_origins or ""
    parsed = {item.strip() for item in raw.split(",") if item.strip()}
    return parsed or {DEFAULT_ALLOWED_ORIGIN}


def _cors_headers(origin: str) -> Dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST,OPTIONS",
        "Access-Control-Allow-Headers": OPTIONS_HEADERS,
        "Vary": "Origin",
    }


def _json_response(status: int, body: Dict[str, Any], origin: str) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            **_cors_headers(origin),
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _empty_response(status: int, origin: str) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": _cors_headers(origin),
        "body": "",
    }


def _get_repo() -> NeonMessageRepository:
    global _repo
    if _repo is None:
        client = get_client(settings.neon_database_url)
        _repo = NeonMessageRepository(client, max_group_languages=settings.max_group_languages)
    return _repo


def _get_ses_client():
    boto3 = importlib.import_module("boto3")
    return boto3.client("sesv2", region_name=os.environ.get("AWS_REGION"))


class TooManyRequestsError(RuntimeError):
    pass
