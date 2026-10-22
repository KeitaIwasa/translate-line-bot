from __future__ import annotations

import logging
from typing import Callable, Optional

import requests

from ..domain.models import CommandDecision
from .gemini_mention_followup import GeminiMentionFollowUp
from .jev_language_reader import read_language_settings

logger = logging.getLogger(__name__)


class JevLanguageMentionFollowUp:
    """言語設定の詳細は Jev で読み、失敗時だけ Gemini に戻す。使い方は Gemini のまま。"""

    def __init__(
        self,
        *,
        gemini: GeminiMentionFollowUp,
        api_key_provider: Callable[[], str],
        model: str = "jev-latest",
        timeout_seconds: float = 1.5,
        min_confidence: float = 0.8,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._gemini = gemini
        self._api_key_provider = api_key_provider
        self._model = model
        self._timeout = timeout_seconds
        self._min_confidence = min_confidence
        self._session = session or requests.Session()

    def complete(self, action: str, router_input: str) -> CommandDecision:
        if action != "language_settings":
            return self._gemini.complete(action, router_input)
        decision, reason = read_language_settings(
            session=self._session,
            api_key_provider=self._api_key_provider,
            model=self._model,
            timeout_seconds=self._timeout,
            min_confidence=self._min_confidence,
            router_input=router_input,
        )
        if reason == "ok" and decision is not None:
            return decision
        # Jev は自由な言語名を生成できない。失敗・低確信度・カタログ外は既存の Gemini 抽出に戻す
        logger.info("Jev language settings fallback | reason=%s", reason)
        return self._gemini.complete(action, router_input)
