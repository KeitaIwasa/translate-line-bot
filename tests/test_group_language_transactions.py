"""言語の置き換えと add_and_remove は、挿入失敗で元の言語を消さない。"""

from contextlib import contextmanager

import pytest

from src.infra.neon_repositories import NeonMessageRepository


class _LanguageStore:
    def __init__(self, languages, *, translation_enabled=False):
        self.languages = dict(languages)
        self.translation_enabled = {"g1": translation_enabled}


class _Conn:
    def __init__(self, store, *, fail_language_insert=False):
        self.store = store
        self.fail_language_insert = fail_language_insert
        self._snapshots = []

    def transaction(self):
        return _Transaction(self)

    def cursor(self):
        return _Cursor(self)


class _Transaction:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn._snapshots.append(
            (dict(self.conn.store.languages), dict(self.conn.store.translation_enabled))
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        languages, enabled = self.conn._snapshots.pop()
        if exc_type:
            self.conn.store.languages = languages
            self.conn.store.translation_enabled = enabled
        return False


class _Cursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        statement = " ".join(str(query).split())
        params = params or ()
        if self.conn.fail_language_insert and statement.startswith("INSERT INTO group_languages"):
            raise RuntimeError("transient failure")
        self._apply(statement, params)

    def executemany(self, query, seq):
        for params in seq:
            self.execute(query, params)

    def fetchall(self):
        return list(self._rows)

    def _apply(self, statement, params):
        store = self.conn.store
        if statement.startswith("DELETE FROM group_languages") and "ANY" in statement:
            group_id, codes = params
            drop = {(group_id, code) for code in codes}
            store.languages = {key: name for key, name in store.languages.items() if key not in drop}
            return
        if statement.startswith("DELETE FROM group_languages"):
            group_id = params[0]
            store.languages = {key: name for key, name in store.languages.items() if key[0] != group_id}
            return
        if statement.startswith("INSERT INTO group_languages"):
            group_id, code, name = params
            store.languages[(group_id, code)] = name
            return
        if statement.startswith("INSERT INTO group_settings"):
            store.translation_enabled[params[0]] = params[1]
            return
        if "SELECT lang_code" in statement and "FROM group_languages" in statement:
            group_id = params[0]
            codes = sorted(code for gid, code in store.languages if gid == group_id)
            self._rows = [(code,) for code in codes]
            return
        raise AssertionError(f"unexpected SQL: {statement}")


class _Client:
    def __init__(self, store, *, fail_language_insert=False):
        self.conn = _Conn(store, fail_language_insert=fail_language_insert)

    @contextmanager
    def connection(self):
        yield self.conn

    @contextmanager
    def cursor(self):
        yield _Cursor(self.conn)


def _repo(store, *, fail_language_insert=False) -> NeonMessageRepository:
    return NeonMessageRepository(
        _Client(store, fail_language_insert=fail_language_insert),
        runtime_cache_ttl_seconds=30,
        clock=lambda: 0.0,
    )


def test_replace_group_languages_keeps_old_languages_when_insert_fails():
    store = _LanguageStore({("g1", "ja"): "Japanese", ("g1", "th"): "Thai"}, translation_enabled=False)
    repo = _repo(store, fail_language_insert=True)

    with pytest.raises(RuntimeError, match="transient failure"):
        repo.replace_group_languages("g1", [("en", "English")])

    assert repo.fetch_group_languages("g1") == ["ja", "th"]
    assert store.translation_enabled["g1"] is False


def test_replace_group_languages_sets_exactly_the_new_languages_and_keeps_translation_on():
    store = _LanguageStore({("g1", "ja"): "Japanese", ("g1", "th"): "Thai"}, translation_enabled=False)
    repo = _repo(store)

    repo.replace_group_languages("g1", [("en", "English")])

    assert repo.fetch_group_languages("g1") == ["en"]
    assert store.translation_enabled["g1"] is True


def test_apply_language_changes_keeps_old_languages_when_add_fails():
    store = _LanguageStore({("g1", "ja"): "Japanese", ("g1", "en"): "English"})
    repo = _repo(store, fail_language_insert=True)

    with pytest.raises(RuntimeError, match="transient failure"):
        repo.apply_language_changes("g1", [("fr", "French")], ["ja"])

    assert repo.fetch_group_languages("g1") == ["en", "ja"]


def test_apply_language_changes_removes_and_adds_in_one_transaction():
    store = _LanguageStore({("g1", "ja"): "Japanese", ("g1", "en"): "English"})
    repo = _repo(store)

    repo.apply_language_changes("g1", [("fr", "French")], ["ja"])

    assert repo.fetch_group_languages("g1") == ["en", "fr"]
