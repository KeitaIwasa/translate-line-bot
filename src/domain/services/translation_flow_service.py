from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime, timezone
from typing import List, Sequence

import requests

from .. import models
from ..ports import MessageRepositoryPort
from .translation_service import TranslationService
from .interface_translation_service import InterfaceTranslationService
from .quota_service import QuotaService, QuotaDecision
from ...presentation.reply_formatter import build_translation_reply
from ...infra.gemini_translation import GeminiRateLimitError
from .retry_policy import RetryPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslationFlowResult:
    decision: QuotaDecision
    reply_text: str | None = None


class TranslationFlowService:
    """グループ翻訳フローを集約し、ハンドラの責務を薄くするサービス。"""

    def __init__(
        self,
        repo: MessageRepositoryPort,
        translation_service: TranslationService,
        interface_translation: InterfaceTranslationService,
        quota_service: QuotaService,
        *,
        max_context_messages: int,
        translation_retry: int,
    ) -> None:
        self._repo = repo
        self._translation = translation_service
        self._interface_translation = interface_translation
        self._quota = quota_service
        self._max_context = max_context_messages
        self._retry_policy = RetryPolicy(max(1, translation_retry))

    def run(
        self,
        *,
        event: models.MessageEvent,
        sender_name: str,
        candidate_languages: Sequence[str],
        paid: bool,
        limit: int,
        plan_key: str,
        period_start: datetime | None,
        period_end: datetime | None,
    ) -> TranslationFlowResult:
        """クオータ判定→翻訳実行→返信文生成までを一括で行う。"""

        decision = self._quota.evaluate(
            group_id=event.group_id or "",
            paid=paid,
            limit=limit,
            period_start=period_start,
            period_end=period_end,
            plan_key=plan_key,
            increment=1,
        )

        if not decision.allowed:
            return TranslationFlowResult(decision=decision, reply_text=None)

        context_messages = self._repo.fetch_recent_messages(event.group_id, self._max_context)
        timestamp = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        translations = self._invoke_translation_with_retry(
            sender_name=sender_name,
            message_text=event.text,
            timestamp=timestamp,
            context=context_messages,
            candidate_languages=candidate_languages,
        )

        if not translations:
            logger.warning(
                "Translation returned no candidates | group=%s user=%s languages=%s plan=%s",
                event.group_id,
                event.user_id,
                list(candidate_languages),
                plan_key,
            )
            return TranslationFlowResult(decision=decision, reply_text=None)

        reply_text = build_translation_reply(event.text, translations)
        return TranslationFlowResult(decision=decision, reply_text=reply_text)

    # --- internal helpers ---
    def _invoke_translation_with_retry(
        self,
        *,
        sender_name: str,
        message_text: str,
        timestamp: datetime,
        context: List[models.ContextMessage],
        candidate_languages: Sequence[str],
    ):
        if not candidate_languages:
            return []

        try:
            return self._retry_policy.run(
                lambda: self._translation.translate(
                    sender_name=sender_name,
                    message_text=message_text,
                    timestamp=timestamp,
                    context_messages=context,
                    candidate_languages=candidate_languages,
                )
            )
        except GeminiRateLimitError:
            raise
        except requests.exceptions.Timeout:
            raise
        except Exception:
            raise
