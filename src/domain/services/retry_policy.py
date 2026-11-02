from __future__ import annotations

import time
from typing import Callable, TypeVar

from ...infra.provider_failure import should_retry_provider_error

T = TypeVar("T")


class RetryPolicy:
    """外部依存向けのリトライ。回復しない失敗は待たずに呼び出し側へ返す。"""

    def __init__(self, retries: int, backoff_base: float = 0.5) -> None:
        self._retries = max(1, retries)
        self._backoff = max(0.0, backoff_base)

    def run(self, func: Callable[[], T]) -> T:
        last_error: Exception | None = None
        for attempt in range(self._retries):
            try:
                return func()
            except Exception as exc:  # pylint: disable=broad-except
                last_error = exc
                # 401/402/403 や 400 は繰り返さない。429 / 5xx / タイムアウトは続ける。
                if not should_retry_provider_error(exc) or attempt >= self._retries - 1:
                    break
                sleep = self._backoff * (attempt + 1)
                if sleep:
                    time.sleep(sleep)
        if last_error:
            raise last_error
        raise RuntimeError("RetryPolicy failed without capturing an exception")
