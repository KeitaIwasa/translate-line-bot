"""翻訳経路の外部依存が失敗したときの分類と、CloudWatch への通知用メトリクス。

401 / 402 / 403 は認証・請求・権限の問題で、同じリクエストを繰り返しても回復しない。
429 / 5xx / タイムアウトは一過性として再試行する。
400 番台のその他はリクエスト不良で、障害アラーム（non_retryable）には載せない。

メトリクスは Embedded Metric Format の JSON を標準出力へ1行だけ出す。
ロガーの接頭辞が付くと CloudWatch がメトリクスとして読まない。
"""

from __future__ import annotations

import json
import sys
import time
import weakref
from dataclasses import dataclass

import requests

from .gemini_errors import GeminiRateLimitError, GeminiRequestError, classify_http_status

METRIC_NAMESPACE = "KOTORI/Translation"
METRIC_NAME = "ProviderFailure"
SIGNAL_VALUE = "provider_failure"

_NEON_NON_RETRYABLE = frozenset(
    {
        "InvalidPassword",
        "InvalidAuthorizationSpecification",
        "InsufficientPrivilege",
    }
)
_NEON_TRANSIENT = frozenset(
    {
        "OperationalError",
        "InterfaceError",
        "ConnectionTimeout",
        "AdminShutdown",
        "CannotConnectNow",
        "TooManyConnections",
    }
)

_recorded: weakref.WeakKeyDictionary[BaseException, "ProviderFailure"] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class ProviderFailure:
    """1回の外部依存失敗。ログとメトリクスで同じ値を使う。"""

    dependency: str
    error_class: str
    status: int | None
    retryable: bool


def classify_provider_failure(
    exc: BaseException,
    *,
    dependency: str | None = None,
) -> ProviderFailure:
    """例外を dependency と error_class に分ける。メトリクスは出さない。"""

    status = _status_code(exc)
    if status is not None:
        error_class, retryable = classify_http_status(status)
    elif _is_transport_error(exc):
        error_class, retryable = "transient", True
    else:
        neon = _neon_failure(exc)
        if neon is not None:
            error_class, retryable = neon
        else:
            error_class, retryable = "unknown", True
    return ProviderFailure(
        dependency=dependency or _infer_dependency(exc),
        error_class=error_class,
        status=status,
        retryable=retryable,
    )


def should_retry_provider_error(exc: BaseException) -> bool:
    """再試行して意味がある失敗か。401/402/403 と 400 は False。"""

    recorded = _existing(exc)
    if recorded is not None:
        return recorded.retryable
    return classify_provider_failure(exc).retryable


def record_provider_failure(
    exc: BaseException,
    *,
    dependency: str | None = None,
    model: str | None = None,
) -> ProviderFailure:
    """失敗を1回だけ記録する。同じ例外の再入ではメトリクスを重ねない。"""

    existing = _existing(exc)
    if existing is not None:
        return existing
    failure = classify_provider_failure(exc, dependency=dependency)
    resolved_model = model if model else getattr(exc, "model", None)
    if not isinstance(resolved_model, str) or not resolved_model.strip():
        resolved_model = None
    _emit(failure, model=resolved_model)
    try:
        _recorded[exc] = failure
    except TypeError:
        # 弱参照できない例外は、呼び出し側が握りつぶす前に1回出していれば足りる
        pass
    return failure


def post_gemini(
    session: requests.Session,
    *,
    model: str,
    url: str,
    params: dict,
    payload: dict,
    timeout: float,
) -> requests.Response:
    """Gemini へ POST し、HTTP エラーは分類してから送出する。"""

    try:
        response = session.post(url, params=params, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        record_provider_failure(exc, dependency="gemini", model=model)
        raise
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and status >= 400:
        if status == 429:
            exc = GeminiRateLimitError(model)
        else:
            exc = GeminiRequestError(model, status)
        record_provider_failure(exc, dependency="gemini", model=model)
        raise exc
    return response


def _emit(failure: ProviderFailure, *, model: str | None) -> None:
    payload: dict = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": METRIC_NAMESPACE,
                    "Dimensions": [
                        ["Signal"],
                        ["ErrorClass"],
                        ["Dependency", "ErrorClass"],
                    ],
                    "Metrics": [{"Name": METRIC_NAME, "Unit": "Count"}],
                }
            ],
        },
        METRIC_NAME: 1,
        "Signal": SIGNAL_VALUE,
        "ErrorClass": failure.error_class,
        "Dependency": failure.dependency,
        "error_class": failure.error_class,
        "dependency": failure.dependency,
        "status": failure.status,
        "retryable": failure.retryable,
        "message": "provider request failed",
    }
    if model:
        payload["model"] = model
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _existing(exc: BaseException) -> ProviderFailure | None:
    try:
        found = _recorded.get(exc)
    except TypeError:
        return None
    if isinstance(found, ProviderFailure):
        return found
    return None


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _infer_dependency(exc: BaseException) -> str:
    module = type(exc).__module__ or ""
    name = type(exc).__name__
    if name in {"GeminiRequestError", "GeminiRateLimitError"} or module.endswith("gemini_errors"):
        return "gemini"
    if name == "LineApiError" or module.endswith("line_api"):
        return "line"
    if module.startswith("psycopg"):
        return "neon"
    return "unknown"


def _is_transport_error(exc: BaseException) -> bool:
    return isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError))


def _neon_failure(exc: BaseException) -> tuple[str, bool] | None:
    module = type(exc).__module__ or ""
    if not module.startswith("psycopg"):
        return None
    names = {cls.__name__ for cls in type(exc).mro()}
    if names & _NEON_NON_RETRYABLE:
        return "non_retryable", False
    if names & _NEON_TRANSIENT:
        return "transient", True
    return "unknown", True
