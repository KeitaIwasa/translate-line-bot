from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Protocol

import requests

from ..domain.models import CommandDecision
from ..domain.ports import CommandRouterPort
from .jev_api import extract_user_message, post_systemone, resolve_api_key

logger = logging.getLogger(__name__)

# Jev に選ばせる操作。キーは CommandDecision.action と一致させる
ACTION_CRITERIA: Dict[str, str] = {
    "language_settings": "Change, add, remove or reset the translation languages",
    "howto": "Asks how to use the bot or asks any question about it",
    "pause": "Pause or stop translating for now",
    "resume": "Resume or restart translating",
    "subscription_menu": "Show or manage the subscription, plan or billing",
    "subscription_cancel": "Cancel the paid (Pro) subscription",
    "subscription_upgrade": "Upgrade to the Pro plan",
    "unknown": "Anything else",
}

# ハンドラーが定型処理する操作。言語設定・使い方以外は Gemini を呼ばない
TEMPLATE_ACTIONS = frozenset(
    {"pause", "resume", "subscription_menu", "subscription_cancel", "subscription_upgrade"}
)
# 分類のあと follow_up で詳細を取る操作（言語は Jev、使い方は Gemini）
FOLLOW_UP_ACTIONS = frozenset({"language_settings", "howto"})


class MentionFollowUp(Protocol):
    def complete(self, action: str, router_input: str) -> CommandDecision:
        """language_settings または howto の詳細を返す。失敗時は action=error。"""


class JevFirstPassCommandRouter(CommandRouterPort):
    """グループメンションを TypeSafe Jev で分類する。

    6系統の扱い:
    - pause / resume とプラン操作（menu / cancel / upgrade）は確信度が十分なら即決する
    - language_settings と howto は Jev の分類を維持し、詳細は follow_up に任せる
    - unknown と低確信度は unknown（既存の未認識応答）
    - Jev の失敗は error（既存の分類失敗応答）
    """

    def __init__(
        self,
        *,
        follow_up: MentionFollowUp,
        api_key_provider: Callable[[], str],
        model: str = "jev-latest",
        timeout_seconds: float = 1.5,
        min_confidence: float = 0.8,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._follow_up = follow_up
        self._api_key_provider = api_key_provider
        self._model = model
        self._timeout = timeout_seconds
        self._min_confidence = min_confidence
        self._session = session or requests.Session()

    def decide(self, text: str) -> CommandDecision:
        started = time.perf_counter()
        choice, confidence, reason = self._classify(text)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if reason == "ok" and choice in TEMPLATE_ACTIONS:
            logger.info(
                "Jev decided | action=%s confidence=%.2f elapsed_ms=%.0f",
                choice,
                confidence,
                elapsed_ms,
            )
            # ack_text と instruction_language は空にして、ハンドラー側の定型文と言語判定に任せる
            return CommandDecision(action=choice, instruction_language="", ack_text="")
        if reason == "needs_follow_up" and choice in FOLLOW_UP_ACTIONS:
            logger.info(
                "Jev follow-up | action=%s confidence=%.2f elapsed_ms=%.0f",
                choice,
                confidence,
                elapsed_ms,
            )
            return self._complete_follow_up(choice, text)
        if reason in {"unknown", "low_confidence"}:
            logger.info(
                "Jev unknown | reason=%s action=%s confidence=%.2f elapsed_ms=%.0f",
                reason,
                choice or "none",
                confidence,
                elapsed_ms,
            )
            return CommandDecision(action="unknown", instruction_language="", ack_text="")
        logger.info(
            "Jev error | reason=%s action=%s confidence=%.2f elapsed_ms=%.0f",
            reason,
            choice or "none",
            confidence,
            elapsed_ms,
        )
        return CommandDecision(action="error", instruction_language="", ack_text="")

    def _complete_follow_up(self, action: str, router_input: str) -> CommandDecision:
        try:
            decision = self._follow_up.complete(action, router_input)
        except Exception:
            logger.warning("Mention follow-up raised | action=%s", action)
            return CommandDecision(action="error", instruction_language="", ack_text="")
        if decision.action == action:
            return decision
        if decision.action == "error":
            return decision
        logger.warning(
            "Mention follow-up action mismatch | expected=%s got=%s",
            action,
            decision.action,
        )
        return CommandDecision(action="error", instruction_language="", ack_text="")

    def _classify(self, text: str) -> tuple[str, float, str]:
        """(action, confidence, reason) を返す。失敗時は action を空にする。"""
        api_key = resolve_api_key(self._api_key_provider)
        if not api_key:
            return "", 0.0, "no_api_key"

        body, reason = post_systemone(
            self._session,
            api_key=api_key,
            model=self._model,
            state={"message": extract_user_message(text)},
            questions={
                "action": {
                    "type": "choice",
                    "instructions": (
                        "A user in a LINE group mentioned a multilingual translation bot "
                        "with this `message`. Which operation do they want the bot to perform?"
                    ),
                    "criteria": ACTION_CRITERIA,
                }
            },
            timeout=self._timeout,
        )
        if body is None:
            return "", 0.0, reason
        try:
            answer = body["answers"]["action"]
            choice = str(answer.get("choice") or "")
            confidence = float(answer.get("confidence") or 0.0)
        except Exception:
            return "", 0.0, "invalid_response"
        if choice not in ACTION_CRITERIA:
            return "", 0.0, "invalid_choice"
        if choice == "unknown":
            return choice, confidence, "unknown"
        if confidence < self._min_confidence:
            return choice, confidence, "low_confidence"
        if choice in FOLLOW_UP_ACTIONS:
            return choice, confidence, "needs_follow_up"
        if choice in TEMPLATE_ACTIONS:
            return choice, confidence, "ok"
        return "", 0.0, "invalid_choice"


class TypesafeApiKeyProvider:
    """TYPESAFE_API_KEY を優先し、無ければ SSM SecureString から一度だけ取得してキャッシュする。"""

    def __init__(
        self,
        *,
        api_key: str = "",
        ssm_parameter_name: str = "",
        ssm_client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._ssm_parameter_name = (ssm_parameter_name or "").strip()
        self._ssm_client_factory = ssm_client_factory
        self._loaded = bool(self._api_key)

    def __call__(self) -> str:
        if self._loaded:
            return self._api_key
        # 失敗しても毎回SSMを叩かないよう、一度で確定させる
        self._loaded = True
        if not self._ssm_parameter_name:
            return ""
        try:
            client = self._ssm_client_factory() if self._ssm_client_factory else _default_ssm_client()
            result = client.get_parameter(Name=self._ssm_parameter_name, WithDecryption=True)
            self._api_key = str(result["Parameter"]["Value"] or "").strip()
        except Exception:
            logger.warning("Failed to load TypeSafe API key from SSM | parameter=%s", self._ssm_parameter_name)
            self._api_key = ""
        return self._api_key


def _default_ssm_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "ssm",
        config=Config(connect_timeout=1, read_timeout=2, retries={"max_attempts": 1}),
    )
