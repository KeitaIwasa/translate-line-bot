from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)

# メンション分類と言語の読み取りで同じエンドポイント・認証を使う
JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def resolve_api_key(provider: Callable[[], str]) -> str:
    """APIキー提供者の失敗は空文字にする。呼び出し側がフォールバックする。"""
    try:
        return (provider() or "").strip()
    except Exception:
        logger.warning("Jev API key provider failed", exc_info=False)
        return ""


def post_systemone(
    session: requests.Session,
    *,
    api_key: str,
    model: str,
    state: Any,
    questions: dict,
    timeout: float,
) -> tuple[Optional[dict], str]:
    """System One を1回呼ぶ。失敗時は (None, reason)。"""
    if not api_key:
        return None, "no_api_key"
    payload = {"state": state, "model": model, "questions": questions}
    try:
        response = session.post(
            JEV_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout:
        return None, "timeout"
    except Exception:
        return None, "request_error"
    if response.status_code != 200:
        return None, f"http_{response.status_code}"
    try:
        body = response.json()
    except Exception:
        return None, "invalid_response"
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        return None, "invalid_response"
    return body, "ok"


def extract_user_message(router_input: str) -> str:
    """ルーター入力(JSON)から user_message を取り出す。JSONでなければそのまま使う。"""
    try:
        data: Any = json.loads(router_input)
    except Exception:
        return router_input
    if isinstance(data, dict) and isinstance(data.get("user_message"), str):
        return data["user_message"]
    return router_input


def extract_current_languages(router_input: str) -> list[str]:
    """ルーター入力から現在の翻訳言語コードを取り出す。"""
    try:
        data: Any = json.loads(router_input)
    except Exception:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("current_languages"), list):
        return []
    codes: list[str] = []
    seen = set()
    for item in data["current_languages"]:
        code = str(item or "").strip().lower()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes
