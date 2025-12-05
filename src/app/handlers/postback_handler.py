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
    def __init__(self, line_client: LinePort, repo: MessageRepositoryPort, max_group_languages: int = 5) -> None:
        self._line = line_client
        self._repo = repo
        self._max_group_languages = max_group_languages

    def handle(self, event: models.PostbackEvent) -> None:
        if not event.data or not event.reply_token or not event.group_id:
            return

        payload = _decode_postback_payload(event.data)
        if not payload or payload.get("kind") != "language_confirm":
            logger.debug("Ignoring unrelated postback", extra={"data": event.data})
            return

        action = payload.get("action")
        primary_language = (payload.get("primary_language") or "").lower()
        if action == "confirm":
            langs = payload.get("languages") or []
            tuples: List[Tuple[str, str]] = _dedup_languages(
                [(item.get("code", ""), item.get("name", "")) for item in langs if item.get("code")]
            )
            if len(tuples) > self._max_group_languages:
                warning = payload.get("limit_text")
                if not warning:
                    warning = (
                        f"You can set up to {self._max_group_languages} translation languages. "
                        f"Please specify {self._max_group_languages} or fewer."
                    )
                self._line.reply_text(event.reply_token, warning)
                return
            completed = self._repo.try_complete_group_languages(event.group_id, tuples)
            if not completed:
                logger.info(
                    "Duplicate language confirmation ignored",
                    extra={"group_id": event.group_id, "languages": [code for code, _ in tuples]},
                )
                return
            self._repo.set_translation_enabled(event.group_id, True)
            text = payload.get("completion_text") or _build_completion_message(tuples)
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
            cancel_text = payload.get("cancel_text") or _build_cancel_message()
            self._line.reply_text(event.reply_token, cancel_text)
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
    filtered = [name for name in names if name]
    if not filtered:
        return "Translation preferences have been saved."
    if len(filtered) == 1:
        joined = filtered[0]
    elif len(filtered) == 2:
        joined = " and ".join(filtered)
    else:
        joined = ", ".join(filtered[:-1]) + ", and " + filtered[-1]
    return f"Enabled translation for {joined}."


def _build_cancel_message() -> str:
    return "Language update has been cancelled. Please tell me all languages again."


def _dedup_languages(languages: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    results: List[Tuple[str, str]] = []
    for code, name in languages:
        lowered = (code or "").lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        results.append((lowered, name))
    return results
