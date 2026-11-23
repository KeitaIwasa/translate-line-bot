from __future__ import annotations

import logging
import select
import socket
import time
from contextlib import contextmanager
from typing import Optional

from psycopg_pool import ConnectionPool

from .neon_endpoint import connection_kwargs, log_neon_endpoint, resolve_neon_dsn, summarize_neon_dsn
from .provider_failure import is_database_connection_failure

logger = logging.getLogger(__name__)

# Neon の compute は約5分で止まる。それより前に捨て、閉じられた接続をクエリに使わない。
# アイドル中の生存確認クエリは打たない。compute を起こさず、温かい経路の往復も増やさない。
IDLE_LIMIT_SECONDS = 240
_CHECKOUT_TIMEOUT_SECONDS = 10


class NeonClient:
    """実行環境をまたいで接続を短時間だけ再利用する psycopg プール。

    読み取りは autocommit にして、文ごとの COMMIT 往復を避ける。
    connection() は複数文と行ロックを一つのトランザクションにまとめる。
    再利用する前に、閉じている接続と長く置いた接続は捨てる。
    それでも接続エラーなら、その操作の最初の文だけを新しい接続で一度やり直す。
    """

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 4) -> None:
        resolved = resolve_neon_dsn(dsn)
        self.endpoint = summarize_neon_dsn(resolved)
        log_neon_endpoint(self.endpoint)
        if resolved != dsn:
            logger.info("Using Neon pooler host | host=%s", self.endpoint.host or "unknown")
        # min_size=1 でコールドスタート時の接続は1本。2本目は翻訳と履歴取得の並行時に開く。
        # プールの寿命は長めにし、使われていない時間は checkout 側で見る。
        # Lambda が凍結している間、背景ワーカーは動かない。
        self._pool = ConnectionPool(
            conninfo=resolved,
            min_size=min_size,
            max_size=max_size,
            kwargs=connection_kwargs(),
            max_idle=IDLE_LIMIT_SECONDS,
            max_lifetime=30 * 60,
            num_workers=1,
            name="neon",
            open=True,
            timeout=_CHECKOUT_TIMEOUT_SECONDS,
        )
        logger.info("Initialized Neon connection pool | min_size=%s max_size=%s", min_size, max_size)

    @contextmanager
    def connection(self):
        """書き込みや FOR UPDATE 用。終了時に commit する。"""

        lease = _Lease(self)
        try:
            try:
                lease.begin()
            except Exception as exc:
                lease.note_failure(exc)
                raise
            try:
                yield _ConnectionProxy(lease)
            except Exception as exc:
                lease.note_failure(exc)
                lease.rollback()
                raise
            else:
                try:
                    lease.commit()
                except Exception as exc:
                    lease.note_failure(exc)
                    raise
        finally:
            lease.release()

    @contextmanager
    def cursor(self):
        """単文の読み書き。autocommit なので COMMIT の往復は発生しない。"""

        lease = _Lease(self)
        proxy = _CursorProxy(lease)
        try:
            try:
                yield proxy
            except Exception as exc:
                lease.note_failure(exc)
                raise
        finally:
            try:
                proxy.close()
            finally:
                lease.release()

    def checkout(self):
        """使える接続を返す。閉じた接続や長く放置した接続は捨てて、新しいものを待つ。"""

        started = time.perf_counter()
        limit = max(1, int(getattr(self._pool, "max_size", 4)))
        discarded = 0
        while True:
            conn = self._pool.getconn(timeout=_CHECKOUT_TIMEOUT_SECONDS)
            reason = connection_unusable_reason(conn, idle_limit=IDLE_LIMIT_SECONDS)
            if reason is None:
                self._log_checkout(started)
                return conn
            logger.warning("discarding stale Neon connection | reason=%s", reason)
            self.discard(conn)
            discarded += 1
            if discarded > limit:
                logger.warning("Neon pool kept returning unusable connections | discarded=%s", discarded)
                conn = self._pool.getconn(timeout=_CHECKOUT_TIMEOUT_SECONDS)
                self._log_checkout(started)
                return conn

    def discard(self, conn) -> None:
        """壊れた接続をプールに戻さず、代わりの接続を作らせる。

        close() したあとに putconn すると、切った接続の状態参照で失敗することがある。
        実プールは閉じる処理と補充を自分の手順で行う。テスト用のプールは putconn に任せる。
        """

        closer = getattr(self._pool, "_close_connection", None)
        schedule = getattr(self._pool, "run_task", None)
        if closer is not None and schedule is not None:
            try:
                from psycopg_pool.pool import AddConnection

                closer(conn)
                schedule(AddConnection(self._pool))
            except Exception:
                logger.warning("failed to discard stale Neon connection", exc_info=True)
            return
        try:
            close = getattr(conn, "close", None)
            if close is not None:
                close()
        except Exception:
            logger.warning("failed to close stale Neon connection", exc_info=True)
        try:
            self._pool.putconn(conn)
        except Exception:
            logger.warning("failed to return stale Neon connection to the pool", exc_info=True)

    def release(self, conn) -> None:
        try:
            setattr(conn, "_kotori_idle_since", time.monotonic())
        except Exception:
            logger.debug("failed to mark Neon connection idle time", exc_info=True)
        self._pool.putconn(conn)

    @staticmethod
    def _log_checkout(started: float) -> None:
        wait_ms = (time.perf_counter() - started) * 1000
        if wait_ms >= 50:
            logger.info("Neon connection checkout | wait_ms=%.2f", wait_ms)


class _Lease:
    """今チェックアウトしている接続。失敗した最初の1回だけ差し替える。"""

    def __init__(self, client: NeonClient) -> None:
        self.client = client
        self.conn = client.checkout()
        self.tx = None
        self.statements = 0
        self.retried = False
        self.broken = False

    def begin(self) -> None:
        try:
            self._enter_transaction()
        except Exception as exc:
            if self.retried or not is_database_connection_failure(exc):
                raise
            logger.warning(
                "Neon transaction did not start; opening a fresh connection | error=%s",
                type(exc).__name__,
            )
            self.replace()
            self._enter_transaction()

    def _enter_transaction(self) -> None:
        self.tx = self.conn.transaction()
        self.tx.__enter__()

    def retry_connection(self, exc: BaseException) -> bool:
        """まだ1文も成功していない接続エラーなら、接続を替えて True を返す。"""

        if self.retried or self.statements or not is_database_connection_failure(exc):
            return False
        logger.warning(
            "Neon connection failed before any statement; retrying once | error=%s",
            type(exc).__name__,
        )
        restart = self.tx is not None
        self.replace()
        if restart:
            self._enter_transaction()
        return True

    def replace(self) -> None:
        self.retried = True
        broken = self.conn
        self.tx = None
        self.conn = None
        self.client.discard(broken)
        self.conn = self.client.checkout()

    def commit(self) -> None:
        tx = self.tx
        self.tx = None
        if tx is not None:
            tx.__exit__(None, None, None)

    def rollback(self) -> None:
        tx = self.tx
        self.tx = None
        if tx is None:
            return
        try:
            tx.__exit__(RuntimeError, RuntimeError("rollback"), None)
        except Exception:
            self.broken = True
            logger.warning("Neon rollback failed", exc_info=True)

    def note_failure(self, exc: BaseException) -> None:
        if is_database_connection_failure(exc) or _is_closed(self.conn):
            self.broken = True

    def release(self) -> None:
        conn = self.conn
        self.conn = None
        if conn is None:
            return
        if self.broken or _is_closed(conn):
            self.client.discard(conn)
            return
        self.client.release(conn)


class _ConnectionProxy:
    """呼び出し側の conn.cursor() / conn.transaction() を、差し替え後の接続へ向ける。"""

    def __init__(self, lease: _Lease) -> None:
        self._lease = lease

    def cursor(self, *args, **kwargs):
        return _CursorProxy(self._lease, args, kwargs)

    def transaction(self, *args, **kwargs):
        return _NestedTransaction(self._lease, args, kwargs)

    def __getattr__(self, name):
        return getattr(self._lease.conn, name)


class _NestedTransaction:
    def __init__(self, lease: _Lease, args, kwargs) -> None:
        self._lease = lease
        self._args = args
        self._kwargs = kwargs
        self._tx = None
        self._conn = None

    def __enter__(self):
        self._conn = self._lease.conn
        self._tx = self._conn.transaction(*self._args, **self._kwargs)
        return self._tx.__enter__()

    def __exit__(self, exc_type, exc, tb):
        # 接続を差し替えたあとは、死んだ接続のセーブポイントを触らない。
        if self._tx is None or self._lease.conn is not self._conn:
            return False
        return self._tx.__exit__(exc_type, exc, tb)


class _CursorProxy:
    def __init__(self, lease: _Lease, args=(), kwargs=None) -> None:
        self._lease = lease
        self._args = args
        self._kwargs = kwargs or {}
        self._cursor = lease.conn.cursor(*self._args, **self._kwargs)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        cursor = self._cursor
        if cursor is None:
            return False
        return cursor.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def execute(self, *args, **kwargs):
        return self._run("execute", args, kwargs)

    def executemany(self, *args, **kwargs):
        return self._run("executemany", args, kwargs)

    def _run(self, method: str, args, kwargs):
        try:
            result = getattr(self._cursor, method)(*args, **kwargs)
        except Exception as exc:
            if not self._lease.retry_connection(exc):
                raise
            self._reopen()
            result = getattr(self._cursor, method)(*args, **kwargs)
        self._lease.statements += 1
        return result

    def _reopen(self) -> None:
        self._cursor = self._lease.conn.cursor(*self._args, **self._kwargs)

    def close(self) -> None:
        cursor = self._cursor
        self._cursor = None
        if cursor is None:
            return
        closer = getattr(cursor, "close", None)
        if closer is None:
            return
        try:
            closer()
        except Exception:
            logger.debug("ignoring cursor close failure", exc_info=True)


def connection_unusable_reason(conn, *, idle_limit: float) -> str | None:
    """クエリを送らずに、再利用してはいけない接続を見分ける。ソケットが無いものは実行時に判断する。"""

    if _is_closed(conn):
        return "closed"
    idle_since = getattr(conn, "_kotori_idle_since", None)
    if isinstance(idle_since, (int, float)) and time.monotonic() - float(idle_since) >= idle_limit:
        return "idle"
    sock = _raw_socket(conn)
    if sock is None:
        return None
    if _socket_unusable(sock):
        return "socket"
    return None


def _is_closed(conn) -> bool:
    if conn is None:
        return True
    try:
        return bool(conn.closed)
    except Exception:
        return False


def _raw_socket(conn):
    pgconn = getattr(conn, "pgconn", None)
    if pgconn is None:
        return None
    try:
        return pgconn.socket
    except Exception:
        return None


def _socket_unusable(sock) -> bool:
    """読めるソケットは、切断かサーバからの未読データなので使わない。"""

    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, TypeError, ValueError):
        return True
    if not readable:
        return False
    try:
        sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
    except BlockingIOError:
        return False
    except InterruptedError:
        return False
    except (OSError, AttributeError):
        return True
    return True


_client: Optional[NeonClient] = None


def get_client(dsn: str) -> NeonClient:
    global _client
    if _client is None:
        _client = NeonClient(dsn)
    return _client
