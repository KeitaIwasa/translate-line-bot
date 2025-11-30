from __future__ import annotations

import base64
import json
import logging
import zlib
from typing import Dict, List, Tuple

from ...domain import models
from ...domain.ports import LinePort, MessageRepositoryPort

logger = logging.getLogger(__name__)


class PostbackHandler:
    def __init__(self, line_client: LinePort, repo: MessageRepositoryPort) -> None:
        self._line = line_client
        self._repo = repo

    def handle(self, event: models.PostbackEvent) -> None:
        if not event.data or not event.reply_token or not event.group_id:
            return

        payload = _decode_postback_payload(event.data)
        if not payload or payload.get("kind") != "language_confirm":
            logger.debug("Ignoring unrelated postback", extra={"data": event.data})
            return

        action = payload.get("action")
        if action == "confirm":
            langs = payload.get("languages") or []
            tuples: List[Tuple[str, str]] = [
                (item.get("code", ""), item.get("name", "")) for item in langs if item.get("code")
            ]
            completed = self._repo.try_complete_group_languages(event.group_id, tuples)
            if not completed:
                logger.info(
                    "Duplicate language confirmation ignored",
                    extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
                )
                return
            self._repo.set_translation_enabled(event.group_id, True)
            text = _build_completion_message(tuples)
            self._line.reply_text(event.reply_token, text)
            logger.info(
                "Language preferences saved",
                extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
            )
        elif action == "cancel":
            cancelled = self._repo.try_cancel_language_prompt(event.group_id)
            if not cancelled:
                logger.info(
                    "Duplicate language cancellation ignored",
                    extra={"group_id": event.group_id, "user_id": event.user_id},
                )
                return
            self._repo.set_translation_enabled(event.group_id, False)
            self._line.reply_text(event.reply_token, _build_cancel_message())
            logger.info("Language enrollment cancelled", extra={"group_id": event.group_id, "user_id": event.user_id})


def _decode_postback_payload(data: str) -> Dict | None:
    if not data.startswith("langpref"):
        return None
    prefix, token = data.split("=", 1)
    padding = "=" * (-len(token) % 4)
    try:
        blob = base64.urlsafe_b64decode(token + padding)
        if prefix == "langpref2":
            blob = zlib.decompress(blob)
        decoded = blob.decode("utf-8")
        return json.loads(decoded)
    except Exception:  # pylint: disable=broad-except
        logger.warning("Failed to decode postback payload", extra={"data": data})
        return None


def _build_completion_message(languages) -> str:
    names = [name for _, name in languages if name]
    joined = "、".join(filter(None, names))
    if joined:
        return f"{joined}の翻訳を有効にしました。"
    return "翻訳設定を保存しました。"


def _build_cancel_message() -> str:
    return "設定を取り消しました。再度、翻訳したい言語をすべて教えてください。"
