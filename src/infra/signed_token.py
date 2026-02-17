from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional


class TokenError(ValueError):
    """署名トークンの検証失敗。"""



def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")



def _b64url_decode(token: str) -> bytes:
    padding = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + padding)



def issue_token(payload: Dict[str, Any], *, secret: str) -> str:
    if not secret:
        raise TokenError("token secret is empty")

    body = dict(payload)
    body.setdefault("iat", int(time.time()))
    encoded = _b64url_encode(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).digest()
    return f"{encoded}.{_b64url_encode(sig)}"



def verify_token(
    token: str,
    *,
    secret: str,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    if not secret:
        raise TokenError("token secret is empty")
    if not token or "." not in token:
        raise TokenError("invalid token format")

    encoded, signature = token.split(".", 1)
    expected = _b64url_encode(hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        raise TokenError("invalid token signature")

    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except Exception as exc:  # pylint: disable=broad-except
        raise TokenError("invalid token payload") from exc

    if not isinstance(payload, dict):
        raise TokenError("invalid token payload type")

    now = int(time.time())
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and int(exp) < now:
        raise TokenError("token expired")

    if scope:
        token_scope = str(payload.get("scope") or "")
        if token_scope != scope:
            raise TokenError("invalid token scope")

    return payload
