import threading
from collections import deque
from datetime import datetime, timezone

import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool

from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.domain.models import TranslationResult
from src.domain.services.quota_service import QuotaDecision, QuotaService
from src.domain.services.translation_flow_service import TranslationFlowService
from src.domain.services.translation_service import TranslationService
from src.infra.neon_client import NeonClient, statement_retry_safe
from src.infra.neon_repositories import NeonMessageRepository


class _Pool:
    """解凍直後のプール。check が失敗した接続は返さない。"""

    def __init__(self, *args, **kwargs):
        self.check_fn = kwargs.get("check")
        self.lock = threading.Lock()
        self.idle = deque()
        self.discarded = 0
        self.applied = 0
        self.quota_statements = 0
        self.positive_quota_statements = 0
        self.message_inserts = 0
        self.queries: list[str] = []
        self.fail_substring = ""
        self.fail_left = 0
        self.languages = ["ja", "en"]
        broken = kwargs.get("_broken", 0)
        for _ in range(broken):
            self.idle.append(_Conn(self, broken=True))

    def connection(self):
        return _Checkout(self)

    def check(self):
        with self.lock:
            kept = deque()
            while self.idle:
                conn = self.idle.popleft()
                try:
                    if self.check_fn is not None:
                        self.check_fn(conn)
                except Exception:
                    self.discarded += 1
                    continue
                kept.append(conn)
            self.idle = kept

    def checkout(self):
        with self.lock:
            while self.idle:
                conn = self.idle.popleft()
                if self.check_fn is None:
                    return conn
                try:
                    self.check_fn(conn)
                except Exception:
                    self.discarded += 1
                    continue
                return conn
            return _Conn(self, broken=False)

    def note_query(self, conn, sql: str, params) -> None:
        self.queries.append(sql)
        if "insert into messages" in sql:
            self.message_inserts += 1
        if "excluded.translation_count" not in sql:
            return
        self.quota_statements += 1
        if self.fail_left and self.fail_substring and self.fail_substring in sql:
            self.fail_left -= 1
            raise errors.OperationalError("SSL connection has been closed unexpectedly")
        amount = int(params[2])
        if amount > 0:
            self.positive_quota_statements += 1
        if conn.in_transaction:
            conn.pending += amount
            return
        self.applied += amount


class _Checkout:
    def __init__(self, pool: _Pool):
        self.pool = pool
        self.conn = None

    def __enter__(self):
        self.conn = self.pool.checkout()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self, pool: _Pool, *, broken: bool):
        self.pool = pool
        self.broken = broken
        self.autocommit = True
        self.in_transaction = False
        self.pending = 0

    def execute(self, query, params=None):
        if self.broken:
            raise errors.OperationalError("SSL connection has been closed unexpectedly")
        if query == "":
            return None
        return self.cursor().execute(query, params)

    def cursor(self):
        return _Cur(self)

    def transaction(self):
        return _Tx(self)


class _Tx:
    def __init__(self, conn: _Conn):
        self.conn = conn

    def __enter__(self):
        self.conn.in_transaction = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.conn.pending = 0
        else:
            with self.conn.pool.lock:
                self.conn.pool.applied += self.conn.pending
        self.conn.pending = 0
        self.conn.in_transaction = False
        return False


class _Cur:
    def __init__(self, conn: _Conn):
        self.conn = conn
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        if self.conn.broken:
            raise errors.OperationalError("SSL connection has been closed unexpectedly")
        sql = " ".join(str(query).split()).lower()
        self.last_sql = sql
        if self.conn.pool.fail_left and self.conn.pool.fail_substring and self.conn.pool.fail_substring in sql:
            if "excluded.translation_count" not in sql:
                self.conn.pool.fail_left -= 1
                self.conn.pool.queries.append(sql)
                raise errors.OperationalError("SSL connection has been closed unexpectedly")
        self.conn.pool.note_query(self.conn, sql, params)

    def executemany(self, query, params_seq):
        for params in params_seq:
            self.execute(query, params)

    def fetchone(self):
        if "usage_rows" in self.last_sql:
            return _prefetch_row(self.conn.pool.languages)
        if "returning translation_count" in self.last_sql:
            return (1,)
        return None

    def fetchall(self):
        return []


def _prefetch_row(languages):
    period_start = datetime(2026, 1, 15, tzinfo=timezone.utc)
    return (
        True,
        None,
        list(languages),
        "active",
        "pro",
        "month",
        False,
        "price_pro",
        period_start,
        datetime(2026, 2, 15, tzinfo=timezone.utc),
        15,
        None,
        None,
        [],
        "Ann",
    )


def _install_pool(monkeypatch, *, broken: int = 0) -> _Pool:
    holder: dict = {}

    class _RecordingPool(_Pool):
        def __init__(self, *args, **kwargs):
            kwargs["_broken"] = broken
            super().__init__(*args, **kwargs)
            holder["pool"] = self

    monkeypatch.setattr("src.infra.neon_client.ConnectionPool", _RecordingPool)
    return holder


def _client(monkeypatch, *, broken: int = 0) -> tuple[NeonClient, _Pool]:
    holder = _install_pool(monkeypatch, broken=broken)
    client = NeonClient("postgres://user:secret@localhost/neondb")
    pool = holder["pool"]
    pool.languages = ["ja", "en"]
    return client, pool


def _repo(client: NeonClient) -> NeonMessageRepository:
    return NeonMessageRepository(client, runtime_cache_ttl_seconds=0, display_name_cache_ttl_seconds=0)


class _Port:
    def __init__(self, *, empty: bool = False):
        self.empty = empty
        self.calls = []

    def translate(self, request):
        self.calls.append(request)
        if self.empty:
            return []
        return [TranslationResult(lang=lang, text=f"t:{lang}") for lang in request.candidate_languages]


class _Line:
    def __init__(self):
        self.replies: list[str] = []

    def reply_text(self, _reply_token, text):
        self.replies.append(text)

    def reply_messages(self, _reply_token, messages):
        text = "\n".join(message.get("text", "") for message in messages if isinstance(message, dict))
        self.reply_text(_reply_token, text)

    def get_display_name(self, *_args, **_kwargs):
        raise AssertionError("display name is already stored")


class _Null:
    def analyze(self, _text):
        return None

    def decide(self, _text):
        return models.CommandDecision(action="unknown")

    def translate(self, *_args, **_kwargs):
        return []


def _event(text: str) -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=int(datetime(2026, 10, 18, tzinfo=timezone.utc).timestamp() * 1000),
        text=text,
        user_id="user",
        group_id="group",
        sender_type="group",
    )


def _handler(repo, line, translator: TranslationService) -> MessageHandler:
    return MessageHandler(
        line_client=line,
        translation_service=translator,
        interface_translation=_Null(),
        language_detector=None,
        language_pref_service=_Null(),
        command_router=_Null(),
        repo=repo,
        max_context_messages=2,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        free_quota_per_month=50,
        standard_quota_per_month=4000,
        pro_quota_per_month=40000,
    )


def _run_message(monkeypatch, text: str, languages: list[str], *, broken: int = 0, empty: bool = False):
    client, pool = _client(monkeypatch, broken=broken)
    pool.languages = list(languages)
    port = _Port(empty=empty)
    service = TranslationService(port)
    line = _Line()
    handler = _handler(_repo(client), line, service)
    try:
        handler.handle(_event(text))
    finally:
        handler._executor.shutdown(wait=True)
    return pool, port, line, service


def test_installed_pool_can_check_connections_before_checkout():
    assert callable(ConnectionPool.check_connection)
    pool = ConnectionPool(
        "dbname=postgres",
        min_size=0,
        max_size=1,
        open=False,
        check=ConnectionPool.check_connection,
    )
    pool.close()


def test_quota_increment_is_not_retried_and_plain_insert_is_not_retried():
    assert statement_retry_safe("SELECT translation_count FROM group_usage_counters FOR UPDATE")
    assert statement_retry_safe(
        """
        INSERT INTO group_members (group_id, user_id, active)
        VALUES (%s, %s, TRUE)
        ON CONFLICT (group_id, user_id)
        DO UPDATE SET active = TRUE
        """
    )
    assert not statement_retry_safe(
        """
        INSERT INTO group_usage_counters (group_id, period_key, translation_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (group_id, period_key)
        DO UPDATE SET translation_count = group_usage_counters.translation_count + EXCLUDED.translation_count
        """
    )
    assert not statement_retry_safe("INSERT INTO messages (group_id, text) VALUES (%s, %s)")


def test_thawed_pool_translates_after_every_stored_connection_is_closed(monkeypatch):
    text = "こんにちは、明日の予定を確認させてください。"
    pool, port, line, _service = _run_message(
        monkeypatch,
        text,
        ["ja", "en"],
        broken=2,
    )

    assert pool.check_fn is ConnectionPool.check_connection
    assert pool.discarded == 2
    assert port.calls
    assert port.calls[0].message_text == text
    assert "en" in port.calls[0].candidate_languages
    assert line.replies
    assert pool.positive_quota_statements == 1
    assert pool.quota_statements == 1
    assert pool.applied == 1
    assert pool.message_inserts == 1


def test_reserve_quota_retries_the_read_and_increments_once(monkeypatch):
    client, pool = _client(monkeypatch, broken=0)
    pool.fail_substring = "for update"
    pool.fail_left = 1

    decision = _repo(client).reserve_quota_slot(
        group_id="group",
        period_key="2026-01-15",
        plan_key="pro",
        stop_translation_on_limit=False,
        limit=40000,
        increment=1,
    )

    assert decision.allowed is True
    assert decision.usage == 1
    assert pool.positive_quota_statements == 1
    assert pool.quota_statements == 1
    assert pool.applied == 1
    assert sum("for update" in query for query in pool.queries) == 2


def test_reserve_quota_does_not_repeat_a_failed_increment(monkeypatch):
    client, pool = _client(monkeypatch, broken=0)
    pool.fail_substring = "excluded.translation_count"
    pool.fail_left = 1

    with pytest.raises(errors.OperationalError):
        _repo(client).reserve_quota_slot(
            group_id="group",
            period_key="2026-01-15",
            plan_key="pro",
            stop_translation_on_limit=False,
            limit=40000,
            increment=1,
        )

    assert pool.quota_statements == 1
    assert pool.positive_quota_statements == 0
    assert pool.applied == 0


_CASES = [
    ("ja", "こんにちは、明日の予定を確認させてください。", ["ja", "en"], True, ""),
    ("en", "Please confirm the schedule by tonight.", ["ja", "en"], True, ""),
    ("th", "พรุ่งนี้เรามีประชุมที่สำนักงานตอนเช้าครับ", ["ja", "th"], True, ""),
    ("my", "မနက်ဖြန် မနက်ဖြန် ရုံးမှာ အစည်းအဝေးရှိပါတယ်။", ["ja", "my"], True, ""),
    ("ne", "हामी भोलि बिहान कार्यालयमा बैठक गर्नेछौं।", ["ja", "ne"], True, ""),
    ("vi", "Ngày mai chúng ta sẽ họp tại văn phòng vào buổi sáng.", ["ja", "vi"], True, ""),
    ("yue", "我哋今晚一齊去飲茶，你得唔得閒呀？", ["ja", "yue"], True, ""),
    ("ca", "Bon dia, avui anem al mercat a comprar pa i fruita fresca.", ["ja", "en"], True, ""),
    ("mix", "明日の会議はオンラインですか？ Please confirm by tonight.", ["ja", "en"], True, ""),
    ("emoji", "😊😂🎉", ["ja", "en"], False, "emoji_only"),
    ("digits", "12345", ["ja", "en"], False, "digits_only"),
    ("blank", " \n\t ", ["ja", "en"], False, "whitespace_only"),
]


@pytest.mark.parametrize(("label", "text", "languages", "expects_reply", "reason"), _CASES)
def test_message_variants_keep_the_existing_translation_flow(
    monkeypatch, caplog, label, text, languages, expects_reply, reason
):
    caplog.set_level("INFO")
    pool, port, line, service = _run_message(monkeypatch, text, languages)

    assert pool.positive_quota_statements == 1
    assert pool.message_inserts == 1
    if expects_reply:
        assert port.calls
        targets = list(port.calls[0].candidate_languages)
        assert targets
        assert set(targets) <= set(languages)
        assert line.replies
        assert pool.applied == 1
        if label == "ja":
            assert targets == ["en"]
        if label == "en":
            assert targets == ["ja"]
        if label == "mix":
            assert targets == ["ja", "en"]
        return

    assert port.calls == []
    assert line.replies == []
    assert pool.applied == 0
    assert service.last_empty_reason == reason
    reason_logs = [
        record.getMessage()
        for record in caplog.records
        if "reason=" in record.getMessage()
        and ("no candidates" in record.getMessage() or "without reply text" in record.getMessage() or "no linguistic" in record.getMessage())
    ]
    assert reason_logs
    assert all(reason in message for message in reason_logs)
    # 空白だけの入力は strip すると空になり、どのログにも含まれるので比べない。
    if text.strip():
        assert text.strip() not in "\n".join(reason_logs)


def test_empty_provider_result_is_logged_without_the_message(caplog):
    caplog.set_level("INFO")
    text = "Please confirm the schedule by tonight."
    port = _Port(empty=True)
    translation = TranslationService(port)

    class _Usage:
        def __init__(self):
            self.rollbacks = 0

        def reserve_quota_slot(self, **kwargs):
            return QuotaDecision(True, False, False, 1, kwargs["limit"], kwargs["period_key"], kwargs["plan_key"])

        def increment_usage(self, _group_id, _period_key, increment=1):
            self.rollbacks += increment
            return 0

    class _Messages:
        def fetch_recent_messages(self, _group_id, _limit):
            return []

    usage = _Usage()
    flow = TranslationFlowService(
        _Messages(),
        translation,
        _Null(),
        QuotaService(usage),
        max_context_messages=2,
        translation_retry=1,
    )
    result = flow.run(
        event=_event(text),
        sender_name="Ann",
        candidate_languages=["ja"],
        plan_key="pro",
        stop_translation_on_limit=False,
        limit=40000,
        period_start=datetime(2026, 1, 15, tzinfo=timezone.utc),
        period_end=datetime(2026, 2, 15, tzinfo=timezone.utc),
    )

    assert result.reply_text is None
    assert result.empty_reason == "empty_provider_result"
    assert usage.rollbacks == -1
    warning = next(record.getMessage() for record in caplog.records if "no candidates" in record.getMessage())
    assert "reason=empty_provider_result" in warning
    assert text not in warning
