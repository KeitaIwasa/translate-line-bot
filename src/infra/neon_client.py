from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

from psycopg_pool import ConnectionPool

from .neon_endpoint import connection_kwargs, log_neon_endpoint, summarize_neon_dsn

logger = logging.getLogger(__name__)


class NeonClient:
    """実行環境をまたいで接続を再利用する psycopg プール。

    読み取りは autocommit にして、文ごとの COMMIT 往復を避ける。
    connection() は複数文と行ロックを一つのトランザクションにまとめる。
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 4) -> None:
        self.endpoint = summarize_neon_dsn(dsn)
        log_neon_endpoint(self.endpoint)
        # min_size=1 でコールドスタート時の接続は1本。2本目は翻訳と履歴取得の並行時に開く。
        # num_workers はプール保守用。Lambda では 1 で足りる。
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs=connection_kwargs(),
            max_idle=240,
            max_lifetime=300,
            num_workers=1,
            name="neon",
            open=True,
        )
        logger.info("Initialized Neon connection pool | min_size=%s max_size=%s", min_size, max_size)

    @contextmanager
    def connection(self):
        """書き込みや FOR UPDATE 用。終了時に commit する。"""

        started = time.perf_counter()
        with self._pool.connection() as conn:
            self._log_checkout(started)
            with conn.transaction():
                yield conn

    @contextmanager
    def cursor(self):
        """単文の読み書き。autocommit なので COMMIT の往復は発生しない。"""

        started = time.perf_counter()
        with self._pool.connection() as conn:
            self._log_checkout(started)
            with conn.cursor() as cur:
                yield cur

    @staticmethod
    def _log_checkout(started: float) -> None:
        wait_ms = (time.perf_counter() - started) * 1000
        if wait_ms >= 50:
            logger.info("Neon connection checkout | wait_ms=%.2f", wait_ms)


_client: Optional[NeonClient] = None


def get_client(dsn: str) -> NeonClient:
    global _client
    if _client is None:
        _client = NeonClient(dsn)
    return _client
