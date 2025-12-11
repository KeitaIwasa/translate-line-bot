from __future__ import annotations

import json
import logging
from typing import Optional

import requests

from ..domain.ports import LinePort

logger = logging.getLogger(__name__)


class LineApiError(RuntimeError):
    pass


class LineApiAdapter(LinePort):
    BASE_URL = "https://api.line.me"

    def __init__(self, channel_access_token: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {channel_access_token}",
                "Content-Type": "application/json",
            }
        )

    def reply_text(self, reply_token: str, text: str) -> None:
        self.reply_messages(reply_token, [{"type": "text", "text": text[:5000]}])

    def reply_messages(self, reply_token: str, messages):  # type: ignore[override]
        url = f"{self.BASE_URL}/v2/bot/message/reply"
        sanitized = [self._sanitize_message(msg) for msg in messages[:5]]
        payload = {"replyToken": reply_token, "messages": sanitized}
        response = self._session.post(url, json=payload, timeout=5)
        if not response.ok:
            body_excerpt = (response.text or "")[:500]
            payload_excerpt = json.dumps(payload, ensure_ascii=False)[:500]
            logger.error(
                "LINE reply failed: status=%s body=%s payload=%s types=%s count=%s",
                response.status_code,
                body_excerpt,
                payload_excerpt,
                [msg.get("type") for msg in sanitized],
                len(sanitized),
            )
            raise LineApiError(f"LINE reply failed with status {response.status_code}")

    def get_display_name(
        self,
        source_type: str,
        container_id: Optional[str],
        user_id: str,
    ) -> Optional[str]:
        display_name, _language = self.get_profile(source_type, container_id, user_id)
        return display_name

    def get_profile(
        self,
        source_type: str,
        container_id: Optional[str],
        user_id: str,
    ) -> tuple[Optional[str], Optional[str]]:
        url = self._build_profile_url(source_type, container_id, user_id)
        response = self._session.get(url, timeout=5)
        if response.status_code == 404:
            return None, None
        if not response.ok:
            logger.warning(
                "Failed to fetch member profile",
                extra={"status": response.status_code, "body": response.text},
            )
            return None, None
        try:
            data = response.json()
        except Exception:  # pylint: disable=broad-except
            logger.warning("Profile response is not JSON", extra={"status": response.status_code})
            return None, None
        return data.get("displayName"), data.get("language")

    def _build_profile_url(self, source_type: str, container_id: Optional[str], user_id: str) -> str:
        if source_type == "group" and container_id:
            return f"{self.BASE_URL}/v2/bot/group/{container_id}/member/{user_id}"
        if source_type == "room" and container_id:
            return f"{self.BASE_URL}/v2/bot/room/{container_id}/member/{user_id}"
        return f"{self.BASE_URL}/v2/bot/profile/{user_id}"

    def get_group_name(self, group_id: str) -> Optional[str]:
        """LINE グループサマリからグループ名を取得する。"""
        url = f"{self.BASE_URL}/v2/bot/group/{group_id}/summary"
        response = self._session.get(url, timeout=5)
        if response.status_code == 404:
            return None
        if not response.ok:
            logger.warning(
                "Failed to fetch group summary",
                extra={"status": response.status_code, "body": response.text},
            )
            return None
        try:
            data = response.json()
        except Exception:  # pylint: disable=broad-except
            logger.warning("Group summary response is not JSON", extra={"status": response.status_code})
            return None
        return data.get("groupName") or data.get("displayName")

    @staticmethod
    def _sanitize_message(message):
        if message.get("type") == "text" and message.get("text"):
            message = {**message, "text": message["text"][:5000]}
        return message
