"""無料枠の上限ちょうどで翻訳を止めず、停止した瞬間に通知を1回だけ出す。"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.domain.models import TranslationRuntimeState
from src.domain.services.quota_service import QuotaDecision, quota_should_notify
from src.infra.neon_repositories import NeonMessageRepository
from src.presentation.free_limit_notice import FREE_LIMIT_BUTTON_LABEL, FREE_LIMIT_HEADLINE


PLAN_URL = "https://plans.example/pro.html?st=token"
FREE_NOTICE_LINE = "Free quota (50 messages per month) is exhausted and translation will stop."


class _Line:
    def __init__(self, *, fail_times: int = 0):
        self.batches: list[list[dict]] = []
        self.fail_times = fail_times
        self._lock = threading.Lock()

    def reply_text(self, _reply_token, text):
        self.reply_messages(_reply_token, [{"type": "text", "text": text}])

    def reply_messages(self, _reply_token, messages):
        with self._lock:
            if self.fail_times:
                self.fail_times -= 1
                raise RuntimeError("line down")
            self.batches.append([dict(message) for message in messages])

    def get_display_name(self, *_args, **_kwargs):
        return None


class _Translator:
    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def translate(self, *, sender_name, message_text, timestamp, context_messages, candidate_languages):
        with self._lock:
            self.calls += 1
        return [models.TranslationResult(lang=lang, text=f"{message_text} ({lang})") for lang in candidate_languages]


class _UI:
    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def translate(self, base_text, target_languages):
        languages = list(target_languages)
        self.calls.append((base_text, languages))
        return [
            models.TranslationResult(lang=lang, text=f"LANG:{lang}:{base_text}")
            for lang in languages
        ]


class _Plans:
    def create_checkout_url(self, group_id: str) -> str:
        return f"{PLAN_URL}&group={group_id}"


class _Prefs:
    def analyze(self, _text: str):
        return None


class _Router:
    def decide(self, _text: str):
        return models.CommandDecision(action="unknown", instruction_language="", ack_text="")


class _Repo:
    """本番の reserve 判定と同じヘルパーで回数と通知権を更新する。"""

    def __init__(self, *, usage: int = 0, languages: list[str] | None = None, notice_plan: str | None = None, enabled: bool = True):
        self.usage = usage
        self.languages = ["en"] if languages is None else list(languages)
        self.notice_plan = notice_plan
        self.translation_enabled = enabled
        self.paused_reason = None if enabled else "quota"
        self._lock = threading.Lock()

    def fetch_group_languages(self, _group_id):
        return list(self.languages)

    def fetch_recent_messages(self, _group_id, _limit):
        return []

    def get_usage(self, _group_id, _period_key):
        return self.usage

    def get_limit_notice_plan(self, _group_id, _period_key):
        return self.notice_plan

    def increment_usage(self, _group_id, _period_key, increment: int = 1):
        with self._lock:
            self.usage += increment
            return self.usage

    def reserve_quota_slot(self, *, group_id, period_key, plan_key, stop_translation_on_limit, limit, increment):
        with self._lock:
            if self.usage >= limit:
                usage = self.usage
                allowed = False
            else:
                self.usage += increment
                usage = self.usage
                allowed = usage <= limit
            notice_plan = self.notice_plan
        return QuotaDecision(
            allowed=allowed,
            should_notify=quota_should_notify(
                allowed=allowed,
                usage=usage,
                limit=limit,
                notice_plan=notice_plan,
                plan_key=plan_key,
                stop_translation_on_limit=stop_translation_on_limit,
            ),
            stop_translation=bool(stop_translation_on_limit and not allowed),
            usage=usage,
            limit=limit,
            period_key=period_key,
            plan_key=plan_key,
        )

    def claim_limit_notice_plan(self, _group_id, _period_key, plan: str) -> bool:
        with self._lock:
            if self.notice_plan == plan:
                return False
            self.notice_plan = plan
            return True

    def release_limit_notice_plan(self, _group_id, _period_key, plan: str) -> None:
        with self._lock:
            if self.notice_plan == plan:
                self.notice_plan = None

    def set_limit_notice_plan(self, _group_id, _period_key, plan: str) -> None:
        self.notice_plan = plan

    def fetch_translation_runtime_state(self, _group_id):
        return TranslationRuntimeState(
            translation_enabled=self.translation_enabled,
            group_languages=list(self.languages),
            subscription_status=None,
            period_start=None,
            period_end=None,
            period_key="2026-11-01",
            usage=self.usage,
            limit_notice_plan=self.notice_plan,
            entitlement_plan="free",
            translation_paused_reason=self.paused_reason,
        )

    def set_translation_enabled(self, _group_id, enabled: bool):
        self.translation_enabled = enabled
        if enabled:
            self.paused_reason = None

    def set_translation_paused_by_quota(self, _group_id):
        self.translation_enabled = False
        self.paused_reason = "quota"

    def is_translation_enabled(self, _group_id):
        return self.translation_enabled

    def get_subscription_period(self, _group_id):
        return (None, None, None)

    def ensure_group_member(self, *_args, **_kwargs):
        return None

    def insert_message(self, *_args, **_kwargs):
        return None

    def get_group_member_display_name(self, *_args, **_kwargs):
        return None

    def upsert_group_member_display_name(self, *_args, **_kwargs):
        return None


def _event(text: str = "hello") -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=int(datetime(2026, 11, 15, tzinfo=timezone.utc).timestamp() * 1000),
        text=text,
        user_id="user",
        group_id="group",
        sender_type="group",
    )


def _handler(repo: _Repo, line: _Line, translator: _Translator, ui: _UI | None = None) -> MessageHandler:
    return MessageHandler(
        line_client=line,
        translation_service=translator,
        interface_translation=ui or _UI(),
        language_detector=None,
        language_pref_service=_Prefs(),
        command_router=_Router(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        free_quota_per_month=50,
        subscription_service=_Plans(),  # type: ignore[arg-type]
    )


def _flex_texts(message: dict) -> list[str]:
    found: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                found.append(node["text"])
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(message.get("contents"))
    return found


def _notice_batches(line: _Line) -> list[list[dict]]:
    def _is_notice(item: dict) -> bool:
        if item.get("type") == "flex":
            return True
        text = item.get("text") or ""
        return FREE_NOTICE_LINE in text or "LANG:" in text

    return [batch for batch in line.batches if any(_is_notice(item) for item in batch)]


def test_quota_should_notify_free_only_when_translation_stops():
    assert quota_should_notify(
        allowed=True,
        usage=50,
        limit=50,
        notice_plan=None,
        plan_key="free",
        stop_translation_on_limit=True,
    ) is False
    assert quota_should_notify(
        allowed=False,
        usage=50,
        limit=50,
        notice_plan=None,
        plan_key="free",
        stop_translation_on_limit=True,
    ) is True
    assert quota_should_notify(
        allowed=False,
        usage=50,
        limit=50,
        notice_plan="free",
        plan_key="free",
        stop_translation_on_limit=True,
    ) is False


def test_quota_should_notify_paid_on_reaching_message():
    assert quota_should_notify(
        allowed=True,
        usage=4000,
        limit=4000,
        notice_plan=None,
        plan_key="standard",
        stop_translation_on_limit=False,
    ) is True
    assert quota_should_notify(
        allowed=False,
        usage=4001,
        limit=4000,
        notice_plan="standard",
        plan_key="standard",
        stop_translation_on_limit=False,
    ) is False


def test_fresh_free_group_notifies_once_when_translation_stops():
    repo = _Repo(usage=0, languages=["en"])
    line = _Line()
    translator = _Translator()
    handler = _handler(repo, line, translator)

    for index in range(50):
        handled = handler._handle_translation_flow(_event(f"message {index}"), sender_name="user")
        assert handled is True

    assert translator.calls == 50
    assert repo.usage == 50
    assert repo.notice_plan is None
    assert repo.translation_enabled is True
    assert _notice_batches(line) == []

    handled = handler._handle_translation_flow(_event("I will find a new translator"), sender_name="user")
    assert handled is True
    assert translator.calls == 50
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"
    assert repo.notice_plan == "free"
    notices = _notice_batches(line)
    assert len(notices) == 1
    flex = notices[0][0]
    assert len(notices[0]) == 1
    assert flex["type"] == "flex"
    action = flex["contents"]["footer"]["contents"][0]["action"]
    assert action["type"] == "uri"
    assert action["label"] == "View plans"
    assert action["uri"] == f"{PLAN_URL}&group=group"
    body = "\n".join(_flex_texts(flex))
    assert "This month's 50 free messages are used up." in body
    assert action["uri"] not in body
    assert action["uri"] not in flex["altText"]

    before = len(line.batches)
    handled = handler._handle_translation_flow(_event("another"), sender_name="user")
    assert handled is True
    assert translator.calls == 50
    assert len(_notice_batches(line)) == 1
    assert len(line.batches) == before


def test_blocked_message_with_null_notice_sends_once():
    repo = _Repo(usage=50, languages=["ja", "en"], notice_plan=None, enabled=True)
    line = _Line()
    translator = _Translator()
    handler = _handler(repo, line, translator)

    handler._handle_translation_flow(_event("still here"), sender_name="user")
    assert repo.notice_plan == "free"
    assert len(_notice_batches(line)) == 1
    assert translator.calls == 0

    handler._handle_translation_flow(_event("again"), sender_name="user")
    assert len(_notice_batches(line)) == 1


def test_paused_group_with_null_notice_self_heals_once():
    repo = _Repo(usage=50, languages=["en"], notice_plan=None, enabled=False)
    line = _Line()
    translator = _Translator()
    handler = _handler(repo, line, translator)

    handler._handle_translation_flow(_event("paused"), sender_name="user")
    handler._handle_translation_flow(_event("paused again"), sender_name="user")

    assert translator.calls == 0
    assert repo.notice_plan == "free"
    assert repo.translation_enabled is False
    assert len(_notice_batches(line)) == 1


def test_flex_failure_falls_back_to_text_without_releasing_claim():
    repo = _Repo(usage=50, languages=["en"], notice_plan=None)
    line = _Line(fail_times=1)
    handler = _handler(repo, line, _Translator())

    handler._handle_translation_flow(_event("first block"), sender_name="user")

    assert repo.notice_plan == "free"
    assert line.batches[0][0]["type"] == "text"
    assert FREE_NOTICE_LINE in line.batches[0][0]["text"]
    assert line.batches[0][1]["text"] == f"{PLAN_URL}&group=group"


def test_failed_notice_release_is_retried_on_next_blocked_message():
    repo = _Repo(usage=50, languages=["en"], notice_plan=None)
    line = _Line(fail_times=2)
    translator = _Translator()
    handler = _handler(repo, line, translator)

    with pytest.raises(RuntimeError, match="line down"):
        handler._handle_translation_flow(_event("first block"), sender_name="user")

    assert repo.notice_plan is None
    assert repo.translation_enabled is False

    handler._handle_translation_flow(_event("retry"), sender_name="user")
    assert repo.notice_plan == "free"
    assert len(_notice_batches(line)) == 1
    assert line.batches[-1][0]["type"] == "flex"


def test_concurrent_messages_at_limit_send_one_notice():
    repo = _Repo(usage=50, languages=["en"])
    line = _Line()
    translator = _Translator()
    handler = _handler(repo, line, translator)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _run():
        try:
            barrier.wait(timeout=2)
            handler._handle_translation_flow(_event("at the limit"), sender_name="user")
        except BaseException as exc:  # noqa: BLE001 - スレッド内の失敗を本体へ返す
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert repo.notice_plan == "free"
    assert len(_notice_batches(line)) == 1
    assert translator.calls == 0


def test_concurrent_reservation_crossing_the_limit_notifies_once():
    repo = _Repo(usage=49, languages=["en"])
    line = _Line()
    translator = _Translator()
    handler = _handler(repo, line, translator)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _run(text: str):
        try:
            barrier.wait(timeout=2)
            handler._handle_translation_flow(_event(text), sender_name="user")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_run, args=(text,)) for text in ("fiftieth", "fifty-first")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert repo.usage == 50
    assert translator.calls == 1
    assert repo.notice_plan == "free"
    assert repo.translation_enabled is False
    assert len(_notice_batches(line)) == 1


@pytest.mark.parametrize(
    "languages",
    [
        ["ja"],
        ["en"],
        ["th"],
        ["my"],
        ["ne"],
        ["vi"],
        ["yue"],
        ["zh-TW"],
        ["ko"],
        ["not-a-language"],
        [],
    ],
)
def test_limit_notice_renders_for_group_languages(languages):
    repo = _Repo(languages=languages)
    line = _Line()
    ui = _UI()
    handler = _handler(repo, line, _Translator(), ui)
    handler._send_limit_reached_notice(_event("ignored"), "free", 50, period_key="2026-11-01")

    assert len(line.batches) == 1
    flex = line.batches[0][0]
    assert flex["type"] == "flex"
    action = flex["contents"]["footer"]["contents"][0]["action"]
    assert action["uri"] == f"{PLAN_URL}&group=group"
    texts = _flex_texts(flex)
    assert all(action["uri"] not in text for text in texts)
    assert action["uri"] not in flex["altText"]
    assert "Unable to generate purchase link" not in flex["altText"]

    non_english = [code.lower() for code in languages if code.lower() != "en"]
    if not non_english:
        # 英語と、言語が1つも無いグループはベース英文をそのまま使う
        assert any("This month's 50 free messages are used up." in text for text in texts)
        assert "2026-12-01" in "\n".join(texts)
        assert ui.calls == []
        assert "LANG:" not in "\n".join(texts)
        assert action["label"] == "View plans"
        return

    translated_bases = [call[0] for call in ui.calls]
    assert FREE_LIMIT_HEADLINE in translated_bases
    assert any(base.startswith("This month's 50 free messages are used up.") for base in translated_bases)
    assert FREE_LIMIT_BUTTON_LABEL in translated_bases
    assert ui.calls[0][1] == non_english
    rendered = "\n".join(texts + [action["label"], flex["altText"]])
    for code in non_english:
        assert f"LANG:{code}:" in rendered


@pytest.mark.parametrize("text", ["Hello こんにちは", "😀🔥", "20261115", "１２３"])
def test_stop_notice_ignores_user_text_shape(text):
    repo = _Repo(usage=50, languages=["ja", "en"], notice_plan=None)
    line = _Line()
    translator = _Translator()
    ui = _UI()
    handler = _handler(repo, line, translator, ui)

    handler._handle_translation_flow(_event(text), sender_name="user")

    assert translator.calls == 0
    assert len(_notice_batches(line)) == 1
    flex = line.batches[0][0]
    rendered = "\n".join(_flex_texts(flex) + [flex["altText"], flex["contents"]["footer"]["contents"][0]["action"]["label"]])
    assert text not in rendered
    assert "This month's 50 free messages are used up." in rendered
    assert "LANG:ja:" in rendered
    assert ui.calls[0][0] == FREE_LIMIT_HEADLINE
    assert flex["contents"]["footer"]["contents"][0]["action"]["uri"].startswith(PLAN_URL)


class _MemoryCursor:
    def __init__(self, store):
        self.store = store
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        sql = " ".join(query.split())
        with self.store["lock"]:
            if sql.startswith("SELECT"):
                found = self.store["rows"].get((params[0], params[1]))
                self.row = None if found is None else (found[0], found[1])
                return
            if "RETURNING translation_count" in sql:
                key = (params[0], params[1])
                count, notice = self.store["rows"].get(key, (0, None))
                count += int(params[2])
                self.store["rows"][key] = (count, notice)
                self.row = (count,)
                return
            if "IS DISTINCT FROM" in sql:
                self.store["claim_sql"] = sql
                key = (params[0], params[1])
                plan = params[2]
                count, notice = self.store["rows"].get(key, (0, None))
                if notice == plan:
                    self.row = None
                    return
                self.store["rows"][key] = (count, plan)
                self.row = (plan,)
                return
            if "limit_notice_plan = NULL" in sql:
                key = (params[0], params[1])
                plan = params[2]
                found = self.store["rows"].get(key)
                if found and found[1] == plan:
                    self.store["rows"][key] = (found[0], None)
                self.row = None
                return
        raise AssertionError(sql)

    def fetchone(self):
        return self.row


class _MemoryConn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _MemoryCursor(self.store)


class _MemoryClient:
    def __init__(self):
        self.store = {"rows": {}, "claim_sql": "", "lock": threading.Lock()}

    def connection(self):
        return _MemoryConn(self.store)

    def cursor(self):
        return _MemoryCursor(self.store)


def test_neon_reserve_defers_free_notice_until_blocked_and_claim_is_once():
    client = _MemoryClient()
    repo = NeonMessageRepository(client)  # type: ignore[arg-type]
    client.store["rows"][("group", "2026-11-01")] = (49, None)

    fiftieth = repo._reserve_quota_slot_once(
        group_id="group",
        period_key="2026-11-01",
        plan_key="free",
        stop_translation_on_limit=True,
        limit=50,
        increment=1,
    )
    assert fiftieth.allowed is True
    assert fiftieth.should_notify is False
    assert fiftieth.stop_translation is False
    assert fiftieth.usage == 50
    assert client.store["rows"][("group", "2026-11-01")] == (50, None)

    blocked = repo._reserve_quota_slot_once(
        group_id="group",
        period_key="2026-11-01",
        plan_key="free",
        stop_translation_on_limit=True,
        limit=50,
        increment=1,
    )
    assert blocked.allowed is False
    assert blocked.should_notify is True
    assert blocked.stop_translation is True
    assert blocked.usage == 50

    assert repo.claim_limit_notice_plan("group", "2026-11-01", "free") is True
    assert repo.claim_limit_notice_plan("group", "2026-11-01", "free") is False
    assert "IS DISTINCT FROM" in client.store["claim_sql"]
    assert client.store["rows"][("group", "2026-11-01")] == (50, "free")

    recorded = repo._reserve_quota_slot_once(
        group_id="group",
        period_key="2026-11-01",
        plan_key="free",
        stop_translation_on_limit=True,
        limit=50,
        increment=1,
    )
    assert recorded.should_notify is False

    repo.release_limit_notice_plan("group", "2026-11-01", "pro")
    assert client.store["rows"][("group", "2026-11-01")][1] == "free"
    repo.release_limit_notice_plan("group", "2026-11-01", "free")
    assert client.store["rows"][("group", "2026-11-01")][1] is None

    healed = repo._reserve_quota_slot_once(
        group_id="group",
        period_key="2026-11-01",
        plan_key="free",
        stop_translation_on_limit=True,
        limit=50,
        increment=1,
    )
    assert healed.should_notify is True


def test_neon_reserve_still_notifies_paid_plan_on_the_reaching_message():
    client = _MemoryClient()
    repo = NeonMessageRepository(client)  # type: ignore[arg-type]
    client.store["rows"][("group", "2026-01-15")] = (3999, None)

    reaching = repo._reserve_quota_slot_once(
        group_id="group",
        period_key="2026-01-15",
        plan_key="standard",
        stop_translation_on_limit=False,
        limit=4000,
        increment=1,
    )
    assert reaching.allowed is True
    assert reaching.should_notify is True
    assert reaching.stop_translation is False

    client.store["rows"][("group", "2026-01-15")] = (4000, "standard")
    blocked = repo._reserve_quota_slot_once(
        group_id="group",
        period_key="2026-01-15",
        plan_key="standard",
        stop_translation_on_limit=False,
        limit=4000,
        increment=1,
    )
    assert blocked.allowed is False
    assert blocked.should_notify is False
    assert blocked.stop_translation is False


def test_neon_claim_allows_only_one_winner_under_concurrency():
    client = _MemoryClient()
    repo = NeonMessageRepository(client)  # type: ignore[arg-type]
    client.store["rows"][("group", "2026-11-01")] = (50, None)
    winners: list[bool] = []
    lock = threading.Lock()

    def _claim():
        won = repo.claim_limit_notice_plan("group", "2026-11-01", "free")
        with lock:
            winners.append(won)

    threads = [threading.Thread(target=_claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert winners.count(True) == 1
    assert client.store["rows"][("group", "2026-11-01")] == (50, "free")
