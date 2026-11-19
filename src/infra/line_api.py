from __future__ import annotations

import logging
from typing import Optional

import requests

from ..domain.ports import LinePort
from .gemini_errors import classify_http_status
from .provider_failure import record_provider_failure

logger = logging.getLogger(__name__)


class LineApiError(RuntimeError):
    """LINE API の失敗。status_code があるときは Gemini と同じ区分で再試行する。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        if isinstance(status_code, int):
            self.error_class, self.retryable = classify_http_status(status_code)
        else:
            self.error_class, self.retryable = "unknown", True


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
        try:
            response = self._session.post(url, json=payload, timeout=5)
        except requests.RequestException as exc:
            record_provider_failure(exc, dependency="line")
            raise
        if not response.ok:
            error_class, _retryable = classify_http_status(response.status_code)
            logger.error(
                "LINE reply failed: status=%s error_class=%s types=%s count=%s",
                response.status_code,
                error_class,
                [msg.get("type") for msg in sanitized],
                len(sanitized),
            )
            error = LineApiError(
                f"LINE reply failed with status {response.status_code}",
                status_code=response.status_code,
            )
            record_provider_failure(error, dependency="line")
            raise error

    def push_text(self, to: str, text: str) -> None:
        url = f"{self.BASE_URL}/v2/bot/message/push"
        payload = {"to": to, "messages": [{"type": "text", "text": (text or '')[:5000]}]}
        response = self._session.post(url, json=payload, timeout=5)
        if not response.ok:
            logger.warning(
                "LINE push failed",
                extra={"status": response.status_code, "body": (response.text or "")[:500], "to": to},
            )

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
