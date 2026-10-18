from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, Optional

import requests

from ..domain.models import CommandDecision
from ..domain.ports import CommandRouterPort

logger = logging.getLogger(__name__)

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"

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

# 定型文で返せる操作（言語非依存）。それ以外は既存のLLMルーターに回す
TEMPLATE_ACTIONS = frozenset(
    {"pause", "resume", "subscription_menu", "subscription_cancel", "subscription_upgrade"}
)


class JevFirstPassCommandRouter(CommandRouterPort):
    """TypeSafe Jev で操作を一次判定し、定型操作だけ即決する。

    言語設定・質問回答・不明・低確信度・Jevエラー/タイムアウト・APIキー未設定の場合は
    既存のルーター（fallback）にそのまま委譲する。
    """

    def __init__(
        self,
        *,
        fallback: CommandRouterPort,
        api_key_provider: Callable[[], str],
        model: str = "jev-latest",
        timeout_seconds: float = 1.5,
        min_confidence: float = 0.8,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._fallback = fallback
        self._api_key_provider = api_key_provider
        self._model = model
        self._timeout = timeout_seconds
        self._min_confidence = min_confidence
        self._session = session or requests.Session()

    def decide(self, text: str) -> CommandDecision:
        started = time.perf_counter()
        choice, confidence, reason = self._classify(text)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if choice in TEMPLATE_ACTIONS and confidence >= self._min_confidence:
            logger.info(
                "Jev first-pass decided | action=%s confidence=%.2f elapsed_ms=%.0f",
                choice,
                confidence,
                elapsed_ms,
            )
            # ack_text と instruction_language は空にして、ハンドラー側の定型文と言語判定に任せる
            return CommandDecision(action=choice, instruction_language="", ack_text="")
        logger.info(
            "Jev first-pass delegated | reason=%s action=%s confidence=%.2f elapsed_ms=%.0f",
            reason,
            choice or "none",
            confidence,
            elapsed_ms,
        )
        return self._fallback.decide(text)

    def _classify(self, text: str) -> tuple[str, float, str]:
        """(action, confidence, reason) を返す。失敗時は action を空にする。"""
        try:
            api_key = (self._api_key_provider() or "").strip()
        except Exception:
            logger.warning("Jev API key provider failed", exc_info=False)
            api_key = ""
        if not api_key:
            return "", 0.0, "no_api_key"

        payload = {
            "state": {"message": _extract_user_message(text)},
            "model": self._model,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": (
                        "A user in a LINE group mentioned a multilingual translation bot "
                        "with this `message`. Which operation do they want the bot to perform?"
                    ),
                    "criteria": ACTION_CRITERIA,
                }
            },
        }
        try:
            response = self._session.post(
                JEV_ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=self._timeout,
            )
        except requests.Timeout:
            return "", 0.0, "timeout"
        except Exception:
            return "", 0.0, "request_error"
        if response.status_code != 200:
            return "", 0.0, f"http_{response.status_code}"
        try:
            answer = response.json()["answers"]["action"]
            choice = str(answer.get("choice") or "")
            confidence = float(answer.get("confidence") or 0.0)
        except Exception:
            return "", 0.0, "invalid_response"
        if choice not in ACTION_CRITERIA:
            return "", 0.0, "invalid_choice"
        if choice not in TEMPLATE_ACTIONS:
            return choice, confidence, "language_dependent"
        if confidence < self._min_confidence:
            return choice, confidence, "low_confidence"
        return choice, confidence, "ok"


def _extract_user_message(router_input: str) -> str:
    """ルーター入力(JSON)から user_message を取り出す。JSONでなければそのまま使う。"""
    try:
        data: Any = json.loads(router_input)
    except Exception:
        return router_input
    if isinstance(data, dict) and isinstance(data.get("user_message"), str):
        return data["user_message"]
    return router_input


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
