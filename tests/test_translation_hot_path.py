import json
import threading
from datetime import datetime, timezone

from psycopg import errors

from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.domain.models import ContextMessage, TranslationResult, TranslationRuntimeState
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.quota_service import QuotaDecision, QuotaService
from src.domain.services.translation_flow_service import TranslationFlowService
from src.domain.services.translation_service import TranslationService
from src.infra.gemini_translation import GeminiTranslationAdapter
from src.infra.neon_endpoint import connection_kwargs, summarize_neon_dsn
from src.infra.neon_repositories import NeonMessageRepository


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.queries: list[str] = []
        self.fail_prefetch_times = 0
        self.raise_undefined = False
        self._pending = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        text = str(query)
        self.queries.append(text)
        if "usage_rows" in text and self.raise_undefined:
            raise errors.UndefinedColumn("combined prefetch column missing")
        if "usage_rows" in text and self.fail_prefetch_times:
            self.fail_prefetch_times -= 1
            raise errors.OperationalError("connection closed")
        self._pending = self.row if "usage_rows" in text else None

    def fetchone(self):
        return self._pending

    def fetchall(self):
        return []


class _Client:
    def __init__(self, row):
        self.cursor_obj = _Cursor(row)

    def cursor(self):
        return self.cursor_obj


def _prefetch_row(**overrides):
    period_start = datetime(2026, 1, 15, tzinfo=timezone.utc)
    row = [
        True,
        None,
        ["ja", "th"],
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
        [
            {
                "period_key": "2026-01-15",
                "translation_count": 4,
                "limit_notice_plan": None,
            }
        ],
        "Ann",
    ]
    for key, value in overrides.items():
        row[key] = value
    return tuple(row)


def _repo(row=None, **kwargs) -> tuple[NeonMessageRepository, _Cursor, _Clock]:
    clock = kwargs.pop("clock", None) or _Clock()
    client = _Client(row if row is not None else _prefetch_row())
    repo = NeonMessageRepository(
        client,
        runtime_cache_ttl_seconds=kwargs.pop("runtime_ttl", 15),
        display_name_cache_ttl_seconds=kwargs.pop("name_ttl", 300),
        clock=clock,
    )
    return repo, client.cursor_obj, clock


def _prefetch_queries(cursor: _Cursor) -> list[str]:
    return [query for query in cursor.queries if "usage_rows" in query]


def test_neon_dsn_summary_reads_region_and_pooler_without_secrets():
    pooled = summarize_neon_dsn(
        "postgres://user:secret@ep-example-pooler.ap-southeast-1.aws.neon.tech/neondb"
    )
    direct = summarize_neon_dsn(
        "postgresql://user:secret@ep-example.ap-northeast-1.aws.neon.tech/neondb?sslmode=require"
    )
    keyword = summarize_neon_dsn("host=ep-example.us-east-1.aws.neon.tech dbname=neondb user=u password=secret")

    assert pooled.host == "ep-example-pooler.ap-southeast-1.aws.neon.tech"
    assert pooled.uses_pooler is True
    assert pooled.region_hint == "ap-southeast-1"
    assert "secret" not in pooled.host
    assert direct.uses_pooler is False
    assert direct.region_hint == "ap-northeast-1"
    assert keyword.region_hint == "us-east-1"
    assert summarize_neon_dsn("postgres://user:secret@localhost/db").region_hint == ""


def test_connection_kwargs_skip_per_statement_commit_and_prepared_statements():
    kwargs = connection_kwargs()
    assert kwargs["autocommit"] is True
    assert kwargs["prepare_threshold"] is None
    assert kwargs["connect_timeout"] == 5


def test_prefetch_reads_settings_plan_usage_and_member_in_one_query():
    repo, cursor, _clock = _repo()

    prefetch = repo.fetch_group_message_prefetch("group", "user")

    assert len(_prefetch_queries(cursor)) == 1
    query = _prefetch_queries(cursor)[0]
    for table in (
        "group_settings",
        "group_languages",
        "group_subscriptions",
        "group_usage_counters",
        "group_members",
    ):
        assert table in query
    assert prefetch.cache_hit is False
    assert prefetch.display_name == "Ann"
    assert prefetch.runtime.translation_enabled is True
    assert prefetch.runtime.group_languages == ["ja", "th"]
    assert prefetch.runtime.subscription_status == "active"
    assert prefetch.runtime.entitlement_plan == "pro"
    assert prefetch.runtime.period_key == "2026-01-15"
    assert prefetch.runtime.usage == 4
    assert prefetch.runtime.quota_anchor_day == 15


def test_prefetch_cache_skips_second_read_until_ttl_or_settings_write():
    repo, cursor, clock = _repo()

    first = repo.fetch_group_message_prefetch("group", "user")
    second = repo.fetch_group_message_prefetch("group", "user")
    assert first.runtime.usage == second.runtime.usage == 4
    assert second.cache_hit is True
    assert len(_prefetch_queries(cursor)) == 1

    clock.now = 10
    repo.fetch_translation_runtime_state("group")
    assert len(_prefetch_queries(cursor)) == 1

    repo.set_translation_enabled("group", False)
    repo.fetch_group_message_prefetch("group", "user")
    assert len(_prefetch_queries(cursor)) == 2

    clock.now = 30
    repo.fetch_group_message_prefetch("group", "user")
    assert len(_prefetch_queries(cursor)) == 3


def test_missing_display_name_is_cached_shorter_than_a_known_name():
    row = _prefetch_row()
    row = row[:-1] + (None,)
    repo, cursor, clock = _repo(row, runtime_ttl=1000, name_ttl=300)

    assert repo.fetch_group_message_prefetch("group", "user").display_name is None
    clock.now = 61
    repo.fetch_group_message_prefetch("group", "user")
    assert len(_prefetch_queries(cursor)) == 2


def test_prefetch_retries_once_when_the_connection_drops():
    repo, cursor, _clock = _repo()
    cursor.fail_prefetch_times = 1

    prefetch = repo.fetch_group_message_prefetch("group", "user")

    assert prefetch.display_name == "Ann"
    assert len(_prefetch_queries(cursor)) == 2


def test_prefetch_falls_back_when_combined_query_is_unsupported(monkeypatch):
    repo, cursor, _clock = _repo()
    cursor.raise_undefined = True
    sentinel = TranslationRuntimeState(
        translation_enabled=True,
        group_languages=["ja"],
        subscription_status=None,
        period_start=None,
        period_end=None,
        period_key="2026-10-01",
        usage=0,
        limit_notice_plan=None,
    )
    monkeypatch.setattr(repo, "_fetch_translation_runtime_state_sequential", lambda _group_id: sentinel)
    monkeypatch.setattr(repo, "get_group_member_display_name", lambda *_args: "Fallback")

    prefetch = repo.fetch_group_message_prefetch("group", "user")

    assert prefetch.runtime is sentinel
    assert prefetch.display_name == "Fallback"


def test_usage_outside_the_recent_window_is_read_directly():
    old_start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    row = _prefetch_row()
    row = list(row)
    row[8] = old_start
    row[13] = [
        {
            "period_key": "2026-01-15",
            "translation_count": 9,
            "limit_notice_plan": "pro",
        }
    ]
    repo, cursor, _clock = _repo(tuple(row))

    def get_usage(_group_id, _period_key):
        cursor.queries.append("direct-usage")
        return 2

    def get_notice(_group_id, _period_key):
        cursor.queries.append("direct-notice")
        return "pro"

    repo.get_usage = get_usage
    repo.get_limit_notice_plan = get_notice

    runtime = repo.fetch_translation_runtime_state("group")

    assert runtime.period_key == "2020-01-01"
    assert runtime.usage == 2
    assert runtime.limit_notice_plan == "pro"
    assert "direct-usage" in cursor.queries
    assert "direct-notice" in cursor.queries


class _Order:
    def __init__(self):
        self._lock = threading.Lock()
        self.events: list[str] = []

    def mark(self, name: str) -> None:
        with self._lock:
            self.events.append(name)


class _HotRepo:
    def __init__(self, order: _Order, display_name: str | None):
        self.order = order
        self.display_name = display_name

    def fetch_group_message_prefetch(self, _group_id, _user_id):
        self.order.mark("prefetch")
        period_start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        return models.GroupMessagePrefetch(
            runtime=TranslationRuntimeState(
                translation_enabled=True,
                group_languages=["ja", "th"],
                subscription_status="active",
                period_start=period_start,
                period_end=datetime(2026, 2, 15, tzinfo=timezone.utc),
                period_key="2026-01-15",
                usage=1,
                limit_notice_plan=None,
                entitlement_plan="pro",
            ),
            display_name=self.display_name,
        )

    def fetch_recent_messages(self, _group_id, _limit):
        self.order.mark("messages")
        return []

    def reserve_quota_slot(self, **kwargs):
        self.order.mark("quota")
        return QuotaDecision(
            True,
            False,
            False,
            1,
            kwargs["limit"],
            kwargs["period_key"],
            kwargs["plan_key"],
        )

    def ensure_group_member(self, *_args, **_kwargs):
        self.order.mark("ensure")

    def upsert_group_member_display_name(self, *_args, **_kwargs):
        self.order.mark("upsert")

    def insert_message(self, *_args, **_kwargs):
        self.order.mark("insert")

    def fetch_translation_runtime_state(self, _group_id):
        raise AssertionError("prefetch already supplied the runtime state")


class _Line:
    def __init__(self, order: _Order, release: threading.Event, entered: threading.Event):
        self.order = order
        self.release = release
        self.entered = entered
        self.display_calls = 0
        self.replies: list[str] = []

    def reply_text(self, _reply_token, text):
        self.entered.wait(timeout=1)
        self.order.mark("reply")
        self.replies.append(text)
        self.release.set()

    def reply_messages(self, _reply_token, messages):
        text = "\n".join(message.get("text", "") for message in messages if isinstance(message, dict))
        self.reply_text(_reply_token, text)

    def get_display_name(self, *_args, **_kwargs):
        self.display_calls += 1
        self.order.mark("line_enter")
        self.entered.set()
        assert self.release.wait(timeout=2)
        self.order.mark("line_exit")
        return "FromLine"


class _Translation:
    def __init__(self):
        self.sender_names: list[str] = []

    def translate(self, *, sender_name, message_text, timestamp, context_messages, candidate_languages, **_kwargs):
        self.sender_names.append(sender_name)
        return [TranslationResult(lang=lang, text=f"{message_text}->{lang}") for lang in candidate_languages]


class _Null:
    def analyze(self, _text):
        return None

    def decide(self, _text):
        return models.CommandDecision(action="unknown")

    def translate(self, *_args, **_kwargs):
        return []


def _event(text: str = "hello") -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=int(datetime(2026, 10, 18, tzinfo=timezone.utc).timestamp() * 1000),
        text=text,
        user_id="user",
        group_id="group",
        sender_type="group",
    )


def _handler(repo, line, translation) -> MessageHandler:
    return MessageHandler(
        line_client=line,
        translation_service=translation,
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


def test_uncached_display_name_does_not_block_the_reply():
    order = _Order()
    release = threading.Event()
    entered = threading.Event()
    repo = _HotRepo(order, display_name=None)
    line = _Line(order, release, entered)
    translation = _Translation()
    handler = _handler(repo, line, translation)

    try:
        handler.handle(_event())
    finally:
        handler._executor.shutdown(wait=True)

    assert translation.sender_names == ["user"]
    assert line.replies
    assert order.events.index("reply") < order.events.index("line_exit")
    assert order.events.index("reply") < order.events.index("upsert")
    assert order.events.index("reply") < order.events.index("insert")
    assert order.events.index("quota") < order.events.index("reply")
    assert "ensure" not in order.events
    assert line.display_calls == 1


def test_cached_display_name_skips_line_and_registers_member_after_reply():
    order = _Order()
    release = threading.Event()
    entered = threading.Event()
    entered.set()
    repo = _HotRepo(order, display_name="Ann")
    line = _Line(order, release, entered)
    translation = _Translation()
    handler = _handler(repo, line, translation)

    try:
        handler.handle(_event())
    finally:
        handler._executor.shutdown(wait=True)

    assert translation.sender_names == ["Ann"]
    assert line.display_calls == 0
    assert order.events.index("reply") < order.events.index("ensure")
    assert order.events.index("quota") < order.events.index("reply")
    assert "upsert" not in order.events


def test_translation_flow_uses_prefetched_context_without_another_read():
    class _Repo:
        def __init__(self):
            self.fetches = 0

        def fetch_recent_messages(self, _group_id, _limit):
            self.fetches += 1
            return []

        def reserve_quota_slot(self, **kwargs):
            return QuotaDecision(True, False, False, 1, kwargs["limit"], kwargs["period_key"], kwargs["plan_key"])

        def increment_usage(self, *_args, **_kwargs):
            return 0

    class _Translator:
        def __init__(self):
            self.context = None

        def translate(self, request):
            self.context = list(request.context_messages)
            return [TranslationResult(lang="ja", text="translated")]

    repo = _Repo()
    translator = _Translator()
    service = TranslationFlowService(
        repo,
        TranslationService(translator),
        InterfaceTranslationService(translator),
        QuotaService(repo),
        max_context_messages=3,
        translation_retry=1,
    )
    context = [ContextMessage(sender_name="Ann", text="earlier", timestamp=datetime(2026, 10, 18, tzinfo=timezone.utc))]

    class _Future:
        def result(self):
            return context

    result = service.run(
        event=_event(),
        sender_name="Ann",
        candidate_languages=["ja"],
        plan_key="pro",
        limit=40000,
        period_start=datetime(2026, 1, 15, tzinfo=timezone.utc),
        period_end=datetime(2026, 2, 15, tzinfo=timezone.utc),
        prefetched_messages=_Future(),
    )

    assert repo.fetches == 0
    assert translator.context == context
    assert result.reply_text


def test_gemini_call_logs_elapsed_time(caplog):
    adapter = GeminiTranslationAdapter("key", "gemini-test", timeout_seconds=5)

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {"translations": [{"lang": "ja", "text": "こんにちは"}]}
                                    )
                                }
                            ]
                        }
                    }
                ]
            }

    adapter._session = type("Session", (), {"post": lambda *_args, **_kwargs: _Response()})()
    request = models.TranslationRequest(
        sender_name="Ann",
        message_text="hello",
        timestamp=datetime(2026, 10, 18, tzinfo=timezone.utc),
        candidate_languages=["ja"],
        context_messages=[],
    )

    with caplog.at_level("INFO"):
        results = adapter.translate(request)

    assert results[0].text == "こんにちは"
    assert any("Gemini call" in record.message and "elapsed_ms=" in record.message for record in caplog.records)


def test_string_usage_rows_are_parsed():
    row = list(_prefetch_row())
    row[13] = json.dumps(row[13])
    repo, _cursor, _clock = _repo(tuple(row))

    runtime = repo.fetch_translation_runtime_state("group")

    assert runtime.usage == 4
