from contextlib import contextmanager

import pytest
from psycopg import errors

from src.infra.neon_client import NeonClient
from src.infra.neon_repositories import (
    NeonMessageRepository,
    _LOCAL_QUOTA_CLAIMS,
    _claim_local,
    _release_local,
)
from src.infra.runtime_cache import MISS


def _sql(query) -> str:
    return " ".join(str(query).split())


class _Cursor:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.owner.execute(_sql(query), params)

    def executemany(self, query, seq):
        statement = _sql(query)
        for index, params in enumerate(seq):
            if self.owner.fail_executemany_after is not None and index >= self.owner.fail_executemany_after:
                raise RuntimeError("transient failure")
            self.owner.execute(statement, params)

    def fetchone(self):
        return self.owner.fetchone()

    def fetchall(self):
        return []


class _Connection:
    def __init__(self, owner):
        self.owner = owner

    def cursor(self):
        return _Cursor(self.owner)


class _BoundaryClient:
    """cursor() は文ごとに確定し、connection() は成功時だけまとめて確定する。"""

    def __init__(self, *, fail_sql=None, undefined_sql=None, fetch_row=(1,), fail_executemany_after=None):
        self.fail_sql = fail_sql
        self.undefined_sql = undefined_sql
        self.fetch_row = fetch_row
        self.fail_executemany_after = fail_executemany_after
        self.committed = []
        self.rolled_back = []
        self.cursor_calls = 0
        self.connection_calls = 0
        self._pending = []
        self._mode = None

    @contextmanager
    def cursor(self):
        self.cursor_calls += 1
        self._mode = "autocommit"
        try:
            yield _Cursor(self)
        finally:
            self._mode = None

    @contextmanager
    def connection(self):
        self.connection_calls += 1
        self._mode = "transaction"
        self._pending = []
        try:
            yield _Connection(self)
        except Exception:
            self.rolled_back.extend(self._pending)
            self._pending = []
            self._mode = None
            raise
        else:
            self.committed.extend(self._pending)
            self._pending = []
            self._mode = None

    def execute(self, statement, _params):
        if self.undefined_sql and self.undefined_sql in statement:
            self.undefined_sql = None
            raise errors.UndefinedColumn("column missing")
        if self.fail_sql and self.fail_sql in statement:
            raise RuntimeError("transient failure")
        if self._mode == "autocommit":
            self.committed.append(statement)
            return
        self._pending.append(statement)

    def fetchone(self):
        return self.fetch_row


def _repo(client) -> NeonMessageRepository:
    return NeonMessageRepository(client, runtime_cache_ttl_seconds=30, clock=lambda: 0.0)


def test_reset_group_language_settings_commits_delete_and_update_together():
    client = _BoundaryClient()
    repo = _repo(client)
    repo._runtime_cache.put("g1", "cached")

    repo.reset_group_language_settings("g1")

    assert client.connection_calls == 1
    assert client.cursor_calls == 0
    assert client.rolled_back == []
    assert [statement.split()[0] for statement in client.committed] == ["DELETE", "UPDATE"]
    assert repo._runtime_cache.get("g1") is MISS


def test_reset_group_language_settings_rolls_back_delete_when_update_fails():
    client = _BoundaryClient(fail_sql="UPDATE group_members")
    repo = _repo(client)
    repo._runtime_cache.put("g1", "cached")

    with pytest.raises(RuntimeError, match="transient failure"):
        repo.reset_group_language_settings("g1")

    assert client.committed == []
    assert len(client.rolled_back) == 1
    assert client.rolled_back[0].startswith("DELETE FROM group_languages")
    assert repo._runtime_cache.get("g1") == "cached"


def test_add_group_languages_rolls_back_earlier_inserts_when_a_later_insert_fails():
    client = _BoundaryClient(fail_executemany_after=1)
    repo = _repo(client)

    with pytest.raises(RuntimeError, match="transient failure"):
        repo.add_group_languages("g1", [("en", "English"), ("ja", "Japanese")])

    assert client.connection_calls == 1
    inserts = [statement for statement in client.committed if statement.startswith("INSERT INTO group_languages")]
    rolled = [statement for statement in client.rolled_back if statement.startswith("INSERT INTO group_languages")]
    assert inserts == []
    assert len(rolled) == 1


def test_schema_fallback_member_writes_stay_on_autocommit_cursor():
    ensure = _BoundaryClient(undefined_sql="active = TRUE")
    _repo(ensure)._ensure_group_member("g1", "u1")
    assert ensure.connection_calls == 0
    assert ensure.cursor_calls == 1
    assert ensure.rolled_back == []
    assert len(ensure.committed) == 1
    assert "DO NOTHING" in ensure.committed[0]

    member = _BoundaryClient(undefined_sql="COALESCE(active", fetch_row=(1,))
    assert _repo(member).is_group_member("g1", "u1") is True
    assert member.connection_calls == 0
    assert member.cursor_calls == 1
    assert len(member.committed) == 1
    assert "COALESCE(active" not in member.committed[0]

    upsert = _BoundaryClient(undefined_sql="left_at = NULL")
    _repo(upsert)._upsert_group_member_display_name("g1", "u1", "Ann")
    assert upsert.connection_calls == 0
    assert upsert.cursor_calls == 1
    assert len(upsert.committed) == 1
    assert "left_at" not in upsert.committed[0]


def test_single_statement_write_stays_on_autocommit_cursor():
    client = _BoundaryClient()
    _repo(client).record_language_prompt("g1")

    assert client.cursor_calls == 1
    assert client.connection_calls == 0
    assert len(client.committed) == 1
    assert "last_prompted_at" in client.committed[0]


class _PoolConnection:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, events, *, fail_second=False):
        self.events = events
        self.fail_second = fail_second
        self.executed = 0

    def transaction(self):
        return _FakeTransaction(self.events)

    def cursor(self):
        return _FakeDriverCursor(self)


class _FakeTransaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("begin")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append("rollback" if exc_type else "commit")
        return False


class _FakeDriverCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.conn.executed += 1
        self.conn.events.append(str(query))
        if self.conn.fail_second and self.conn.executed == 2:
            raise RuntimeError("transient failure")


def _client(monkeypatch, conn) -> NeonClient:
    class _Pool:
        def __init__(self, *args, **kwargs):
            pass

        def connection(self):
            return _PoolConnection(conn)

    monkeypatch.setattr("src.infra.neon_client.ConnectionPool", _Pool)
    return NeonClient("postgres://user:secret@localhost/neondb")


def test_neon_client_connection_rolls_back_when_the_second_statement_fails(monkeypatch):
    events = []
    client = _client(monkeypatch, _FakeConnection(events, fail_second=True))

    with pytest.raises(RuntimeError, match="transient failure"):
        with client.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM group_languages")
                cur.execute("UPDATE group_members")

    assert events == [
        "begin",
        "DELETE FROM group_languages",
        "UPDATE group_members",
        "rollback",
    ]


def test_release_approaching_notice_sets_the_flag_false():
    client = _BoundaryClient()
    repo = _repo(client)

    repo.release_approaching_notice("g1", "2026-10-01")

    statement = client.committed[-1]
    assert "approaching_notice_sent = FALSE" in statement
    assert "WHERE group_id = %s AND period_key = %s" in statement


def test_release_quota_mention_reminder_clears_only_that_date():
    client = _BoundaryClient()
    repo = _repo(client)

    repo.release_quota_mention_reminder("g1", "2026-10-01", "2026-10-22")

    statement = client.committed[-1]
    assert "quota_mention_reminder_on = NULL" in statement
    assert "quota_mention_reminder_on = %s" in statement


def test_release_falls_back_to_dropping_the_local_claim_when_the_column_is_missing():
    _LOCAL_QUOTA_CLAIMS.clear()
    approaching = ("approaching", "g1", "2026-10-01")
    mention = ("mention", "g1", "2026-10-01", "2026-10-22")
    assert _claim_local(approaching) is True
    assert _claim_local(mention) is True
    try:
        approaching_repo = _repo(_BoundaryClient(undefined_sql="approaching_notice_sent"))
        approaching_repo.release_approaching_notice("g1", "2026-10-01")
        assert approaching not in _LOCAL_QUOTA_CLAIMS
        assert mention in _LOCAL_QUOTA_CLAIMS

        mention_repo = _repo(_BoundaryClient(undefined_sql="quota_mention_reminder_on"))
        mention_repo.release_quota_mention_reminder("g1", "2026-10-01", "2026-10-22")
        assert mention not in _LOCAL_QUOTA_CLAIMS
        assert _claim_local(approaching) is True
        assert _claim_local(mention) is True
    finally:
        _LOCAL_QUOTA_CLAIMS.clear()


def test_neon_client_cursor_does_not_open_a_transaction(monkeypatch):
    events = []
    client = _client(monkeypatch, _FakeConnection(events))

    with client.cursor() as cur:
        cur.execute("SELECT 1")

    assert events == ["SELECT 1"]
