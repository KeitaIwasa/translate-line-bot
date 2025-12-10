from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryPolicy:
    """シンプルなリトライポリシー。エラーを呼び出し側に再送出する。"""

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
                sleep = self._backoff * (attempt + 1)
                if attempt < self._retries - 1 and sleep:
                    time.sleep(sleep)
        if last_error:
            raise last_error
        raise RuntimeError("RetryPolicy failed without capturing an exception")
