from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class LineApiError(RuntimeError):
    pass


class LineApiClient:
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
        url = f"{self.BASE_URL}/v2/bot/message/reply"
        payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]}
        response = self._session.post(url, json=payload, timeout=5)
        if not response.ok:
            logger.error("LINE reply failed", extra={"status": response.status_code, "body": response.text})
            raise LineApiError(f"LINE reply failed with status {response.status_code}")

    def get_display_name(
        self,
        source_type: str,
        container_id: Optional[str],
        user_id: str,
    ) -> Optional[str]:
        if source_type == "group" and container_id:
            url = f"{self.BASE_URL}/v2/bot/group/{container_id}/member/{user_id}"
        elif source_type == "room" and container_id:
            url = f"{self.BASE_URL}/v2/bot/room/{container_id}/member/{user_id}"
        else:
            url = f"{self.BASE_URL}/v2/bot/profile/{user_id}"
        response = self._session.get(url, timeout=5)
        if response.status_code == 404:
            return None
        if not response.ok:
            logger.warning(
                "Failed to fetch member profile",
                extra={"status": response.status_code, "body": response.text},
            )
            return None
        data = response.json()
        return data.get("displayName")
