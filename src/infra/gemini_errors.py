from __future__ import annotations

import requests

# 認証・請求・権限。同じ本体では回復しない。
NON_RETRYABLE_HTTP_STATUS = frozenset({401, 402, 403})


def classify_http_status(status_code: int) -> tuple[str, bool]:
    """HTTP ステータスを error_class と再試行可否にする。

    400 はリクエスト不良で、non_retryable（課金・認証アラーム）には入れない。
    戻り値は (error_class, retryable)。
    """

    if status_code in NON_RETRYABLE_HTTP_STATUS:
        return "non_retryable", False
    if status_code in {408, 429} or 500 <= status_code <= 599:
        return "transient", True
    if 400 <= status_code <= 499:
        return "bad_request", False
    return "unknown", True


class GeminiRequestError(requests.HTTPError):
    """Gemini APIのHTTPエラー（URLや認証情報を含めない）。"""

    def __init__(self, model: str, status_code: int) -> None:
        self.model = model
        self.status_code = status_code
        self.error_class, self.retryable = classify_http_status(status_code)
        super().__init__(f"Gemini request failed: model={model} status={status_code}")


class GeminiRateLimitError(GeminiRequestError):
    """GeminiがHTTP 429を返したときのエラー。"""

    def __init__(self, model: str) -> None:
        super().__init__(model, 429)
