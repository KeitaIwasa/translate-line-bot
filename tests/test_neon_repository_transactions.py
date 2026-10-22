from contextlib import contextmanager
from unittest.mock import MagicMock

from src.infra.neon_repositories import NeonMessageRepository


class _FakeClient:
    """cursor()（autocommit）と connection()（トランザクション）の利用を記録する。"""

    def __init__(self):
        self.calls = []
        self.cur = MagicMock()

    @contextmanager
    def cursor(self):
        self.calls.append("cursor")
        yield self.cur

    @contextmanager
    def connection(self):
        self.calls.append("connection")
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = self.cur
        yield conn


def test_reset_group_language_settings_runs_in_one_transaction():
    client = _FakeClient()
    repo = NeonMessageRepository(client)

    repo.reset_group_language_settings("gid")

    assert client.calls == ["connection"]
    assert client.cur.execute.call_count == 2


def test_add_group_languages_runs_in_one_transaction():
    client = _FakeClient()
    repo = NeonMessageRepository(client)

    repo.add_group_languages("gid", [("en", "English"), ("ja", "Japanese")])

    # 既存言語の読み取りは autocommit、書き込みはトランザクション
    assert client.calls[-1] == "connection"
    client.cur.executemany.assert_called_once()
