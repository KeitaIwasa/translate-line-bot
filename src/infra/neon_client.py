from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

from psycopg import errors
from psycopg_pool import ConnectionPool

from .neon_endpoint import connection_kwargs, log_neon_endpoint, summarize_neon_dsn

logger = logging.getLogger(__name__)

# 3.2 以降。取り出し前に死んだ接続を捨てる。古いプールでは属性が無い。
_CONNECTION_CHECK = getattr(ConnectionPool, "check_connection", None)

def _normalize_sql(query) -> str:
    return " ".join(str(query).split()).strip().lower()


def _sql_kind(query) -> str:
    text = _normalize_sql(query)
    if not text:
        return "unknown"
    return text.split(" ", 1)[0]


def statement_retry_safe(query) -> bool:
    """切れた接続で同じ文をやり直してよいか。

    読み取りと、同じ値の再実行で結果が変わらない ON CONFLICT だけを許可する。
    利用回数の加算や素の INSERT は、サーバが受信済みでもクライアントが結果を
    読めなかったときに二重になるので許可しない。
    """

    text = _normalize_sql(query)
    if text.startswith("select") or text.startswith("show") or text.startswith("explain"):
        return True
    if text.startswith("with") and not any(word in text for word in ("insert", "update", "delete")):
        return True
    if text.startswith("insert") and "on conflict" in text and "+" not in text:
        return True
    return False


class NeonClient:
    """実行環境をまたいで接続を再利用する psycopg プール。

    読み取りは autocommit にして、文ごとの COMMIT 往復を避ける。
    connection() は複数文と行ロックを一つのトランザクションにまとめる。
    Lambda の凍結中はプールの保守が止まるので、解凍直後の接続は切れていることがある。
    取り出し時に検査し、まだ確定していない安全な文だけ新しい接続で1回やり直す。
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 4) -> None:
        self.endpoint = summarize_neon_dsn(dsn)
        log_neon_endpoint(self.endpoint)
        # min_size=1 でコールドスタート時の接続は1本。2本目は翻訳と履歴取得の並行時に開く。
        # num_workers はプール保守用。Lambda では 1 で足りる。
        pool_kwargs = {}
        if callable(_CONNECTION_CHECK):
            pool_kwargs["check"] = _CONNECTION_CHECK
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
            **pool_kwargs,
        )
        logger.info("Initialized Neon connection pool | min_size=%s max_size=%s", min_size, max_size)

    def discard_broken_connections(self) -> None:
        """プールに残っている切れた接続を捨てる。補充はプールのワーカーが行う。"""

        check = getattr(self._pool, "check", None)
        if not callable(check):
            return
        try:
            check()
        except Exception:
            logger.warning("Neon pool check failed after a closed connection", exc_info=True)

    @contextmanager
    def connection(self):
        """書き込みや FOR UPDATE 用。終了時に commit する。"""

        started = time.perf_counter()
        proxy = _TransactionSession(self)
        proxy.open()
        self._log_checkout(started)
        try:
            yield proxy
        except BaseException as exc:
            proxy.abort(exc)
            raise
        else:
            proxy.commit()
        finally:
            proxy.close()

    @contextmanager
    def cursor(self):
        """単文の読み書き。autocommit なので COMMIT の往復は発生しない。"""

        started = time.perf_counter()
        session = _AutocommitSession(self)
        session.open()
        self._log_checkout(started)
        try:
            yield session
        finally:
            session.close()

    @staticmethod
    def _log_checkout(started: float) -> None:
        wait_ms = (time.perf_counter() - started) * 1000
        if wait_ms >= 50:
            logger.info("Neon connection checkout | wait_ms=%.2f", wait_ms)

    def _checkout(self):
        return self._pool.connection()


class _RetryState:
    """1回の取り出しの中で、安全な文だけを1回やり直す。"""

    def __init__(self) -> None:
        self.done: list[tuple] = []
        self.retried = False

    def note(self, query, params, *, many: bool) -> None:
        self.done.append((query, params, many))


class _ConnectionHolder:
    def __init__(self, client: NeonClient) -> None:
        self._client = client
        self._conn_cm = None
        self.conn = None
        self._tx = None
        self._close_error: Optional[BaseException] = None
        self.generation = 0

    def open(self) -> None:
        self._conn_cm = self._client._checkout()
        self.conn = self._conn_cm.__enter__()
        self.generation += 1

    def begin(self) -> None:
        self._tx = self.conn.transaction()
        self._tx.__enter__()

    def _release(self, exc: Optional[BaseException]) -> None:
        info = (type(exc), exc, exc.__traceback__) if exc is not None else (None, None, None)
        tx = self._tx
        conn_cm = self._conn_cm
        self._tx = None
        self._conn_cm = None
        self.conn = None
        if tx is not None:
            try:
                tx.__exit__(*info)
            except Exception:
                logger.warning("Neon transaction close failed", exc_info=True)
        if conn_cm is not None:
            try:
                conn_cm.__exit__(*info)
            except Exception:
                logger.warning("Neon connection close failed", exc_info=True)

    def abort(self, exc: BaseException) -> None:
        self._release(exc)

    def commit(self) -> None:
        tx = self._tx
        self._tx = None
        if tx is None:
            return
        try:
            tx.__exit__(None, None, None)
        except BaseException as exc:
            self._close_error = exc
            raise

    def close(self) -> None:
        self._release(self._close_error)


class _StatementCursor:
    """connection() が返す接続のカーソル。失敗した安全な文だけ接続を取り直す。"""

    def __init__(self, session: "_TransactionSession") -> None:
        self._session = session
        self._cur_cm = None
        self._cur = None
        self._generation = -1

    def __enter__(self):
        self._ensure_cursor()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._close_cursor()
        return False

    def _ensure_cursor(self) -> None:
        if self._cur is not None and self._generation == self._session.holder.generation:
            return
        self._close_cursor()
        self._cur_cm = self._session.holder.conn.cursor()
        self._cur = self._cur_cm.__enter__()
        self._generation = self._session.holder.generation

    def _close_cursor(self) -> None:
        cur_cm = self._cur_cm
        self._cur = None
        self._cur_cm = None
        if cur_cm is None:
            return
        try:
            cur_cm.__exit__(None, None, None)
        except Exception:
            logger.warning("Neon cursor close failed", exc_info=True)

    def execute(self, query, params=None):
        self._run(query, params, many=False)

    def executemany(self, query, params_seq):
        self._run(query, list(params_seq), many=True)

    def _run(self, query, params, *, many: bool) -> None:
        self._ensure_cursor()
        try:
            self._invoke(query, params, many)
        except errors.OperationalError:
            if not self._session.recover(query, params, many, self._invoke):
                raise
        self._session.state.note(query, params, many=many)

    def _invoke(self, query, params, many: bool) -> None:
        self._ensure_cursor()
        if many:
            self._cur.executemany(query, params)
            return
        self._cur.execute(query, params)

    def fetchone(self):
        self._ensure_cursor()
        return self._cur.fetchone()

    def fetchall(self):
        self._ensure_cursor()
        return self._cur.fetchall()


class _TransactionSession:
    def __init__(self, client: NeonClient) -> None:
        self.holder = _ConnectionHolder(client)
        self.state = _RetryState()
        self._client = client

    def open(self) -> None:
        self.holder.open()
        self.holder.begin()

    def cursor(self):
        return _StatementCursor(self)

    def abort(self, exc: BaseException) -> None:
        self.holder.abort(exc)

    def commit(self) -> None:
        self.holder.commit()

    def close(self) -> None:
        self.holder.close()

    def recover(self, query, params, many: bool, invoke) -> bool:
        if self.state.retried or not statement_retry_safe(query):
            return False
        if any(not statement_retry_safe(previous) for previous, _, _ in self.state.done):
            return False
        self.state.retried = True
        logger.warning(
            "Closed Neon connection discarded; retrying once | kind=%s",
            _sql_kind(query),
        )
        self.holder.abort(errors.OperationalError("connection closed"))
        self._client.discard_broken_connections()
        self.holder.open()
        self.holder.begin()
        for previous, previous_params, previous_many in self.state.done:
            invoke(previous, previous_params, previous_many)
        invoke(query, params, many)
        return True


class _AutocommitSession:
    def __init__(self, client: NeonClient) -> None:
        self._client = client
        self._holder = _ConnectionHolder(client)
        self._state = _RetryState()
        self._cur_cm = None
        self._cur = None

    def open(self) -> None:
        self._holder.open()
        self._cur_cm = self._holder.conn.cursor()
        self._cur = self._cur_cm.__enter__()

    def close(self) -> None:
        cur_cm = self._cur_cm
        self._cur = None
        self._cur_cm = None
        if cur_cm is not None:
            try:
                cur_cm.__exit__(None, None, None)
            except Exception:
                logger.warning("Neon cursor close failed", exc_info=True)
        self._holder.close()

    def execute(self, query, params=None):
        self._run(query, params, many=False)

    def executemany(self, query, params_seq):
        self._run(query, list(params_seq), many=True)

    def _run(self, query, params, *, many: bool) -> None:
        try:
            self._invoke(query, params, many)
        except errors.OperationalError:
            if not self._recover(query, params, many):
                raise
        self._state.note(query, params, many=many)

    def _invoke(self, query, params, many: bool) -> None:
        if many:
            self._cur.executemany(query, params)
            return
        self._cur.execute(query, params)

    def _recover(self, query, params, many: bool) -> bool:
        if self._state.retried or not statement_retry_safe(query):
            return False
        self._state.retried = True
        logger.warning(
            "Closed Neon connection discarded; retrying once | kind=%s",
            _sql_kind(query),
        )
        self._close_quietly()
        self._client.discard_broken_connections()
        self.open()
        # autocommit で成功した文は確定済みなので、失敗した文だけをやり直す。
        self._invoke(query, params, many)
        return True

    def _close_quietly(self) -> None:
        cur_cm = self._cur_cm
        self._cur = None
        self._cur_cm = None
        if cur_cm is not None:
            try:
                cur_cm.__exit__(None, None, None)
            except Exception:
                logger.warning("Neon cursor close failed", exc_info=True)
        self._holder.abort(errors.OperationalError("connection closed"))

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


_client: Optional[NeonClient] = None


def get_client(dsn: str) -> NeonClient:
    global _client
    if _client is None:
        _client = NeonClient(dsn)
    return _client
