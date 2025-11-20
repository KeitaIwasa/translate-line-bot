from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator, Optional

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


class NeonClient:
    """Thin wrapper around psycopg ConnectionPool for Neon."""

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 4) -> None:
        self._pool = ConnectionPool(conninfo=dsn, min_size=min_size, max_size=max_size)
        logger.debug("Initialized Neon connection pool")

    @contextmanager
    def connection(self):  # type: ignore[override]
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def cursor(self):  # type: ignore[override]
        with self.connection() as conn:
            with conn.cursor() as cur:
                yield cur


_client: Optional[NeonClient] = None


def get_client(dsn: str) -> NeonClient:
    global _client
    if _client is None:
        _client = NeonClient(dsn)
    return _client
