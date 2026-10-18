from __future__ import annotations

from typing import Callable, Generic, Hashable, TypeVar

T = TypeVar("T")

# キャッシュ未登録と、None を保存した状態を区別する。
MISS = object()


class TtlCache(Generic[T]):
    """Lambda 実行環境のメモリに載せる短い TTL キャッシュ。"""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float]) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[Hashable, tuple[float, T]] = {}

    def get(self, key: Hashable):
        item = self._items.get(key)
        if item is None:
            return MISS
        expires_at, value = item
        if self._clock() >= expires_at:
            self._items.pop(key, None)
            return MISS
        return value

    def put(self, key: Hashable, value: T, *, ttl: float | None = None) -> None:
        effective = self._ttl if ttl is None else ttl
        if effective <= 0:
            return
        self._items[key] = (self._clock() + effective, value)

    def invalidate(self, key: Hashable) -> None:
        self._items.pop(key, None)
