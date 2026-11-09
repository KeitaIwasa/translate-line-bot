import socket
import time

import psycopg
import pytest
from psycopg import errors

from src.infra.neon_client import IDLE_LIMIT_SECONDS, NeonClient, connection_unusable_reason
from src.infra.neon_endpoint import pooler_host, resolve_neon_dsn
from src.infra.provider_failure import is_database_connection_failure


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        if self.conn.fail_begin:
            self.conn.fail_begin = False
            raise psycopg.OperationalError("SSL connection has been closed unexpectedly")
        self.conn.events.append("begin")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.events.append("rollback" if exc_type else "commit")
        return False


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.conn.queries.append(query)
        if self.conn.fail_times:
            self.conn.fail_times -= 1
            error = self.conn.error or psycopg.OperationalError(
                "consuming input failed: SSL connection has been closed unexpectedly"
            )
            raise error

    def executemany(self, query, seq):
        self.execute(query, seq)

    def fetchone(self):
        return (self.conn.name,)


class _Conn:
    def __init__(self, name, *, fail_times=0, closed=False, error=None, fail_begin=False):
        self.name = name
        self.fail_times = fail_times
        self.closed = closed
        self.error = error
        self.fail_begin = fail_begin
        self.queries = []
        self.events = []

    def cursor(self):
        return _Cursor(self)

    def transaction(self):
        return _Tx(self)

    def close(self):
        self.closed = True


class _Pool:
    def __init__(self, conns, **kwargs):
        self.conns = list(conns)
        self.max_size = kwargs.get("max_size", 4)
        self.kwargs = kwargs
        self.conninfo = kwargs.get("conninfo")
        self.discarded = []
        self.returned = []
        self.checkouts = 0

    def getconn(self, timeout=None):
        self.checkouts += 1
        if not self.conns:
            raise AssertionError("pool is empty")
        return self.conns.pop(0)

    def putconn(self, conn):
        if getattr(conn, "closed", False):
            self.discarded.append(conn)
            return
        self.returned.append(conn)


def _client(monkeypatch, pool) -> NeonClient:
    monkeypatch.setattr("src.infra.neon_client.ConnectionPool", lambda *args, **kwargs: pool)
    if "conninfo" not in pool.kwargs:
        pool.kwargs["conninfo"] = "postgres://user:secret@localhost/neondb"
    return NeonClient(pool.kwargs["conninfo"])


def test_closed_spare_connections_are_discarded_before_the_query(monkeypatch):
    dead_a = _Conn("dead-a", closed=True)
    dead_b = _Conn("dead-b", closed=True)
    live = _Conn("live")
    pool = _Pool([dead_a, dead_b, live])
    client = _client(monkeypatch, pool)

    with client.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == ("live",)

    assert dead_a.queries == []
    assert dead_b.queries == []
    assert live.queries == ["SELECT 1"]
    assert [conn.name for conn in pool.discarded] == ["dead-a", "dead-b"]


def test_ssl_disconnect_retries_the_statement_once_on_a_fresh_connection(monkeypatch):
    stale = _Conn("stale", fail_times=1)
    fresh = _Conn("fresh")
    pool = _Pool([stale, fresh])
    client = _client(monkeypatch, pool)

    with client.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == ("fresh",)

    assert stale.queries == ["SELECT 1"]
    assert fresh.queries == ["SELECT 1"]
    assert [conn.name for conn in pool.discarded] == ["stale"]
    assert pool.checkouts == 2


def test_connection_failure_is_not_retried_more_than_once(monkeypatch):
    first = _Conn("first", fail_times=5)
    second = _Conn("second", fail_times=5)
    third = _Conn("third")
    pool = _Pool([first, second, third])
    client = _client(monkeypatch, pool)

    with pytest.raises(psycopg.OperationalError):
        with client.cursor() as cur:
            cur.execute("SELECT 1")

    assert first.queries == ["SELECT 1"]
    assert second.queries == ["SELECT 1"]
    assert third.queries == []


def test_query_error_is_not_retried(monkeypatch):
    conn = _Conn("live", fail_times=1, error=errors.QueryCanceled("cancel"))
    spare = _Conn("spare")
    pool = _Pool([conn, spare])
    client = _client(monkeypatch, pool)

    with pytest.raises(errors.QueryCanceled):
        with client.cursor() as cur:
            cur.execute("SELECT 1")

    assert spare.queries == []
    assert pool.checkouts == 1


def test_idle_connection_is_discarded_without_running_the_query(monkeypatch):
    stale = _Conn("stale")
    stale._kotori_idle_since = time.monotonic() - (IDLE_LIMIT_SECONDS + 1)
    fresh = _Conn("fresh")
    pool = _Pool([stale, fresh])
    client = _client(monkeypatch, pool)

    with client.cursor() as cur:
        cur.execute("SELECT 1")

    assert stale.queries == []
    assert fresh.queries == ["SELECT 1"]
    assert [conn.name for conn in pool.discarded] == ["stale"]


def test_transaction_start_failure_retries_once_before_the_body(monkeypatch):
    stale = _Conn("stale", fail_begin=True)
    fresh = _Conn("fresh")
    pool = _Pool([stale, fresh])
    client = _client(monkeypatch, pool)

    with client.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM group_languages")
            cur.execute("UPDATE group_members")

    assert stale.queries == []
    assert fresh.queries == ["DELETE FROM group_languages", "UPDATE group_members"]
    assert "begin" in fresh.events
    assert fresh.events[-1] == "commit"
    assert [conn.name for conn in pool.discarded] == ["stale"]


def test_socket_eof_is_unusable_without_a_query():
    left, right = socket.socketpair()
    try:
        right.close()
        conn = _Conn("sock")
        conn.pgconn = type("Pg", (), {"socket": left})()
        conn.closed = False
        assert connection_unusable_reason(conn, idle_limit=IDLE_LIMIT_SECONDS) == "socket"
    finally:
        left.close()


def test_quiet_socket_stays_usable():
    left, right = socket.socketpair()
    try:
        conn = _Conn("sock")
        conn.pgconn = type("Pg", (), {"socket": left})()
        conn.closed = False
        assert connection_unusable_reason(conn, idle_limit=IDLE_LIMIT_SECONDS) is None
    finally:
        left.close()
        right.close()


def test_pooler_host_is_applied_to_neon_and_not_to_local(monkeypatch):
    monkeypatch.delenv("NEON_USE_POOLER", raising=False)
    direct = "postgres://user:secret@ep-example.ap-northeast-1.aws.neon.tech/neondb?sslmode=require"
    rewritten = resolve_neon_dsn(direct)
    assert rewritten == (
        "postgres://user:secret@ep-example-pooler.ap-northeast-1.aws.neon.tech/neondb?sslmode=require"
    )
    assert resolve_neon_dsn(
        "postgres://user:secret@ep-example-pooler.ap-northeast-1.aws.neon.tech/neondb"
    ).count("-pooler") == 1
    assert resolve_neon_dsn("postgres://user:secret@localhost/neondb") == "postgres://user:secret@localhost/neondb"
    keyword = resolve_neon_dsn(
        "host=ep-example.us-east-1.aws.neon.tech dbname=neondb user=u password=secret"
    )
    assert "host=ep-example-pooler.us-east-1.aws.neon.tech" in keyword
    assert "password=secret" in keyword

    monkeypatch.setenv("NEON_USE_POOLER", "false")
    assert resolve_neon_dsn(direct) == direct
    assert pooler_host("ep-example.c-3.ap-southeast-1.aws.neon.tech") == (
        "ep-example-pooler.c-3.ap-southeast-1.aws.neon.tech"
    )


def test_client_passes_the_pooler_dsn_to_the_pool(monkeypatch):
    captured = {}

    class _RecordingPool:
        def __init__(self, conninfo="", **kwargs):
            captured["conninfo"] = conninfo
            captured["max_idle"] = kwargs.get("max_idle")
            self.max_size = kwargs.get("max_size", 4)

    monkeypatch.delenv("NEON_USE_POOLER", raising=False)
    monkeypatch.setattr("src.infra.neon_client.ConnectionPool", _RecordingPool)
    NeonClient("postgresql://user:secret@ep-example.ap-northeast-1.aws.neon.tech/neondb")

    assert "ep-example-pooler.ap-northeast-1.aws.neon.tech" in captured["conninfo"]
    assert "secret" in captured["conninfo"]
    assert captured["max_idle"] == IDLE_LIMIT_SECONDS


def test_discard_uses_the_pool_close_path_instead_of_putconn(monkeypatch):
    stale = _Conn("stale", closed=True)
    fresh = _Conn("fresh")

    class _RealishPool:
        def __init__(self, *args, **kwargs):
            self.max_size = kwargs.get("max_size", 4)
            self._queue = [stale, fresh]
            self.tasks = []
            self.returned = []

        def getconn(self, timeout=None):
            return self._queue.pop(0)

        def putconn(self, returned):
            if returned.closed:
                raise AssertionError("closed connection must not be putconn")
            self.returned.append(returned.name)

        def _close_connection(self, returned):
            returned.close()

        def run_task(self, task):
            self.tasks.append(type(task).__name__)

    pool = _RealishPool()
    monkeypatch.setattr("src.infra.neon_client.ConnectionPool", lambda *args, **kwargs: pool)
    client = NeonClient("postgres://user:secret@localhost/neondb")

    with client.cursor() as cur:
        cur.execute("SELECT 1")

    assert fresh.queries == ["SELECT 1"]
    assert pool.tasks == ["AddConnection"]
    assert pool.returned == ["fresh"]


def test_connection_failure_classifier_skips_query_cancel():
    assert is_database_connection_failure(
        psycopg.OperationalError("consuming input failed: SSL connection has been closed unexpectedly")
    )
    assert is_database_connection_failure(psycopg.InterfaceError("connection already closed"))
    assert is_database_connection_failure(errors.AdminShutdown("shutdown"))
    assert not is_database_connection_failure(errors.QueryCanceled("cancel"))
    assert not is_database_connection_failure(RuntimeError("transient failure"))
