from __future__ import annotations

import requests


class GeminiRequestError(requests.HTTPError):
    """Gemini APIのHTTPエラー（URLや認証情報を含めない）。"""

    def __init__(self, model: str, status_code: int) -> None:
        self.model = model
        self.status_code = status_code
        super().__init__(f"Gemini request failed: model={model} status={status_code}")


class GeminiRateLimitError(GeminiRequestError):
    """GeminiがHTTP 429を返したときのエラー。"""

    def __init__(self, model: str) -> None:
        super().__init__(model, 429)
