"""言語の置き換えと add_and_remove は、挿入失敗で元の言語を消さない。"""

import time
from contextlib import contextmanager
from urllib.parse import urlencode

import pytest

from src.app.handlers.postback_handler import PostbackHandler
from src.domain import models
from src.domain.services.language_settings_service import LanguageSettingsService
from src.group_language_handler import handle_group_language_request
from src.infra.neon_repositories import NeonMessageRepository
from src.infra.signed_token import issue_token


class _LanguageStore:
    def __init__(self, languages, *, translation_enabled=False):
        self.languages = dict(languages)
        self.translation_enabled = {"g1": translation_enabled}
        # チャット確認の世代。prompted が確認ボタン、completed があると閉じている
        self.markers = {}
        self.clock = 0


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
        store = self.conn.store
        self.conn._snapshots.append(
            (
                dict(store.languages),
                dict(store.translation_enabled),
                {key: dict(value) for key, value in store.markers.items()},
            )
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        languages, enabled, markers = self.conn._snapshots.pop()
        if exc_type:
            self.conn.store.languages = languages
            self.conn.store.translation_enabled = enabled
            self.conn.store.markers = markers
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
        statement = _normalize_sql(query)
        params = params or ()
        if self.conn.fail_language_insert and statement.startswith("INSERT INTO group_languages"):
            raise RuntimeError("transient failure")
        self._apply(statement, params)

    def executemany(self, query, seq):
        for params in seq:
            self.execute(query, params)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

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
        if statement.startswith("INSERT INTO group_members"):
            self._apply_member(store, statement, params)
            return
        if statement.startswith("SELECT last_prompted_at"):
            marker = store.markers.get(params[0], {"prompted": None, "completed": None})
            self._rows = [(marker["prompted"], marker["completed"])]
            return
        if statement.startswith("UPDATE group_members"):
            marker = store.markers.setdefault(params[0], {"prompted": None, "completed": None})
            marker["completed"] = _tick(store)
            return
        if "SELECT lang_code, lang_name" in statement and "FROM group_languages" in statement:
            group_id = params[0]
            rows = [(code, name) for (gid, code), name in store.languages.items() if gid == group_id]
            self._rows = sorted(rows)
            return
        if "SELECT lang_code" in statement and "FROM group_languages" in statement:
            group_id = params[0]
            codes = sorted(code for gid, code in store.languages if gid == group_id)
            self._rows = [(code,) for code in codes]
            return
        raise AssertionError(f"unexpected SQL: {statement}")

    def _apply_member(self, store, statement, params):
        group_id = params[0]
        marker = store.markers.setdefault(group_id, {"prompted": None, "completed": None})
        if "DO NOTHING" in statement:
            return
        if "last_completed_at = NULL" in statement:
            stamp = _tick(store)
            marker["prompted"] = stamp
            marker["completed"] = None
            self._rows = [(stamp,)]
            return
        if "last_prompted_at" in statement and "last_completed_at" in statement:
            stamp = _tick(store)
            marker["prompted"] = stamp
            marker["completed"] = stamp
            return
        raise AssertionError(f"unexpected member SQL: {statement}")


def _tick(store) -> int:
    store.clock += 1
    return store.clock


def _normalize_sql(query) -> str:
    text = str(query)
    # psycopg の sql.SQL は str() が SQL('\\n INSERT ...') になる
    if text.startswith("SQL(") and text.endswith(")"):
        inner = text[4:-1].strip()
        if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in {"'", '"'}:
            inner = inner[1:-1]
        text = inner.replace("\\n", " ").replace("\\t", " ")
    return " ".join(text.split())


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
    store.markers["g1"] = {"prompted": 1, "completed": None}
    repo = _repo(store, fail_language_insert=True)

    with pytest.raises(RuntimeError, match="transient failure"):
        repo.replace_group_languages("g1", [("en", "English")])

    assert repo.fetch_group_languages("g1") == ["ja", "th"]
    assert store.translation_enabled["g1"] is False
    # 挿入に失敗した確認は開いたまま。世代も進まない
    assert store.markers["g1"] == {"prompted": 1, "completed": None}


def test_replace_group_languages_sets_exactly_the_new_languages_and_keeps_translation_on():
    store = _LanguageStore({("g1", "ja"): "Japanese", ("g1", "th"): "Thai"}, translation_enabled=False)
    repo = _repo(store)

    repo.replace_group_languages("g1", [("en", "English")])

    assert repo.fetch_group_languages("g1") == ["en"]
    assert store.translation_enabled["g1"] is True
    assert store.markers["g1"]["prompted"] == store.markers["g1"]["completed"]
    assert store.markers["g1"]["completed"] is not None


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


def test_open_prompt_without_a_stamp_still_applies_once():
    store = _LanguageStore({("g1", "ja"): "Japanese"})
    repo = _repo(store)

    applied = repo.try_complete_group_languages("g1", [("ja", "Japanese"), ("en", "English")])
    duplicate = repo.try_complete_group_languages("g1", [("th", "Thai"), ("en", "English")])

    assert applied.status == "applied"
    assert duplicate.status == "duplicate"
    assert repo.fetch_group_languages("g1") == ["en", "ja"]


def test_replace_bumps_generation_so_an_older_confirmation_cannot_overwrite():
    store = _LanguageStore({("g1", "ja"): "Japanese", ("g1", "en"): "English"})
    repo = _repo(store)
    issued = repo.record_language_prompt("g1")

    repo.replace_group_languages("g1", [("ja", "Japanese"), ("zh", "Chinese")])
    stale = repo.try_complete_group_languages(
        "g1",
        [("en", "English"), ("th", "Thai")],
        issued_at=issued,
    )
    assert stale.status == "stale"
    assert repo.fetch_group_languages("g1") == ["ja", "zh"]

    # 保存のあとで開き直した確認だけが通る。古いボタンは新しい世代でも無効
    reopened = repo.record_language_prompt("g1")
    still_stale = repo.try_complete_group_languages(
        "g1",
        [("en", "English"), ("th", "Thai")],
        issued_at=issued,
    )
    applied = repo.try_complete_group_languages(
        "g1",
        [("en", "English"), ("th", "Thai")],
        issued_at=reopened,
    )

    assert still_stale.status == "stale"
    assert applied.status == "applied"
    assert repo.fetch_group_languages("g1") == ["en", "th"]


class _ReplyLine:
    def __init__(self):
        self.replies = []

    def reply_text(self, _token, text):
        self.replies.append(text)


class _Subs:
    def create_language_settings_url(self, *_args, **_kwargs):
        return ""


def _page_post(repo, languages):
    token = issue_token(
        {
            "version": 2,
            "group_id": "g1",
            "scope": "group_languages",
            "exp": int(time.time()) + 3600,
        },
        secret="secret",
    )
    pairs = [("st", token)] + [("languages", code) for code in languages]
    body = urlencode(pairs)
    return handle_group_language_request(
        {
            "requestContext": {"http": {"method": "POST"}},
            "queryStringParameters": {"st": token},
            "body": body,
        },
        repo=repo,
        line=None,
        secret="secret",
    )


def _confirm(repo, line, issued_at, languages):
    payload = LanguageSettingsService._encode_postback_payload(
        {
            "kind": "language_confirm",
            "action": "confirm",
            "languages": [{"code": code, "name": name} for code, name in languages],
            "primary_language": "ja",
            "issued_at": issued_at,
        }
    )
    PostbackHandler(
        line,
        repo,
        interface_translation=object(),
        subscription_service=_Subs(),
    ).handle(
        models.PostbackEvent(
            event_type="postback",
            reply_token="reply",
            group_id="g1",
            user_id="u1",
            sender_type="user",
            data=payload,
        )
    )


def test_page_save_and_unchanged_post_make_old_ok_button_stale():
    store = _LanguageStore({("g1", "ja"): "Japanese", ("g1", "en"): "English"})
    repo = _repo(store)
    line = _ReplyLine()
    issued = repo.record_language_prompt("g1")

    unchanged = _page_post(repo, ["ja", "en"])
    assert unchanged["statusCode"] == 200
    _confirm(repo, line, issued, [("fr", "French"), ("de", "German")])
    assert repo.fetch_group_languages("g1") == ["en", "ja"]
    assert line.replies
    assert "その確認は古いよ" in line.replies[-1]

    saved = _page_post(repo, ["ja", "zh"])
    assert saved["statusCode"] == 200
    before = len(line.replies)
    _confirm(repo, line, issued, [("en", "English"), ("th", "Thai")])
    assert repo.fetch_group_languages("g1") == ["ja", "zh"]
    assert len(line.replies) == before + 1
    assert "その確認は古いよ" in line.replies[-1]

    reopened = repo.record_language_prompt("g1")
    _confirm(repo, line, reopened, [("en", "English"), ("th", "Thai")])
    assert repo.fetch_group_languages("g1") == ["en", "th"]
    assert "その確認は古いよ" not in line.replies[-1]

    # 今のボタンをもう一度押しても、言語は変わらず、断りも足さない
    replies = len(line.replies)
    _confirm(repo, line, reopened, [("ko", "Korean"), ("zh", "Chinese")])
    assert repo.fetch_group_languages("g1") == ["en", "th"]
    assert len(line.replies) == replies
