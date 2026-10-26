"""外部依存の失敗分類、再試行、メトリクス。翻訳入力の回帰も含む。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src.app.handlers.message_handler import (
    RATE_LIMIT_MESSAGE,
    MessageHandler,
    _last_rate_limit_message,
)
from src.domain import models
from src.domain.models import TranslationResult
from src.domain.services.language_detection_service import LanguageDetectionService
from src.domain.services.retry_policy import RetryPolicy
from src.domain.services.translation_service import TranslationService
from src.infra.gemini_errors import GeminiRateLimitError, GeminiRequestError, classify_http_status
from src.infra.gemini_translation import GeminiTranslationAdapter
from src.infra.language_pref_client import LanguagePreferenceAdapter
from src.infra.line_api import LineApiAdapter, LineApiError
from src.infra.provider_failure import (
    METRIC_NAME,
    METRIC_NAMESPACE,
    SIGNAL_VALUE,
    classify_provider_failure,
    record_provider_failure,
    should_retry_provider_error,
)

ROOT = Path(__file__).resolve().parents[1]

# 翻訳そのものの回帰。失敗の区分は本文に依存しないことを、入力ごとに確認する。
TRANSLATION_SAMPLES = [
    ("ja", "こんにちは、今日は良い天気です"),
    ("en", "Hello, how are you today?"),
    ("th", "สวัสดีครับ วันนี้อากาศดีมาก"),
    ("zh", "你好，今天天气很好"),
    ("zh-tw", "你好，今天天氣很好"),
    ("ko", "안녕하세요, 오늘 날씨가 좋아요"),
    ("vi", "Xin chào, hôm nay trời đẹp"),
    ("id", "Halo, cuaca hari ini sangat bagus"),
    ("my", "မင်္ဂလာပါ၊ ဒီနေ့ မိုးလေဝသ ကောင်းပါတယ်"),
    ("ar", "مرحبا، الطقس جميل اليوم"),
    ("unregistered", "Szia, ma nagyon szép az idő"),
    ("mixed", "Good morning. おはよう。"),
    ("emoji", "😀😀😀"),
    ("numbers", "12345 67890"),
    ("long", ("hello " * 400).strip()),
    ("empty", ""),
    ("whitespace", " \n\t "),
]

HTTP_CASES = [
    (401, "non_retryable", False),
    (402, "non_retryable", False),
    (403, "non_retryable", False),
    (400, "bad_request", False),
    (404, "bad_request", False),
    (408, "transient", True),
    (429, "transient", True),
    (500, "transient", True),
    (502, "transient", True),
    (503, "transient", True),
]

# 入力ごとに通す失敗。None はタイムアウト。
FAILURE_CASES = [
    (401, "non_retryable", 1),
    (402, "non_retryable", 1),
    (403, "non_retryable", 1),
    (400, "bad_request", 1),
    (429, "transient", 3),
    (500, "transient", 3),
    (503, "transient", 3),
    (None, "transient", 3),
]


class _StatusResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self) -> dict:
        return {}


class _StatusSession:
    def __init__(self, status_code: int | None) -> None:
        self.status_code = status_code
        self.calls = 0

    def post(self, url, params=None, json=None, timeout=None):  # noqa: A002
        self.calls += 1
        if self.status_code is None:
            raise requests.Timeout("timed out")
        return _StatusResponse(self.status_code)


class _FakeTranslator:
    def __init__(self) -> None:
        self.calls = []

    def translate(self, request):
        self.calls.append(request)
        lang = request.candidate_languages[0]
        return [TranslationResult(lang=lang, text=f"translated:{lang}")]


def _failures(stdout: str) -> list[dict]:
    found = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if payload.get("message") == "provider request failed":
            found.append(payload)
    return found


@pytest.mark.parametrize("status,error_class,retryable", HTTP_CASES)
def test_http_status_classification(status, error_class, retryable):
    assert classify_http_status(status) == (error_class, retryable)
    exc = GeminiRequestError("gemini-3.5-flash-lite", status)
    failure = classify_provider_failure(exc)
    assert failure.dependency == "gemini"
    assert failure.error_class == error_class
    assert failure.status == status
    assert failure.retryable is retryable
    assert should_retry_provider_error(exc) is retryable
    assert exc.error_class == error_class
    assert exc.retryable is retryable


def test_record_emits_one_structured_line_and_is_idempotent(capsys):
    exc = GeminiRequestError("gemini-3.5-flash-lite", 402)
    first = record_provider_failure(exc, dependency="gemini")
    second = record_provider_failure(exc)
    assert first is second
    assert first.error_class == "non_retryable"
    assert first.retryable is False

    payloads = _failures(capsys.readouterr().out)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["error_class"] == "non_retryable"
    assert payload["status"] == 402
    assert payload["dependency"] == "gemini"
    assert payload["ErrorClass"] == "non_retryable"
    assert payload["Dependency"] == "gemini"
    assert payload["Signal"] == SIGNAL_VALUE
    assert payload["model"] == "gemini-3.5-flash-lite"
    assert payload[METRIC_NAME] == 1
    assert "api-key" not in json.dumps(payload)
    metric = payload["_aws"]["CloudWatchMetrics"][0]
    assert metric["Namespace"] == METRIC_NAMESPACE
    assert metric["Metrics"][0]["Name"] == METRIC_NAME
    flat = {name for group in metric["Dimensions"] for name in group}
    assert flat == {"Signal", "ErrorClass", "Dependency"}
    assert "status" not in flat
    assert "model" not in flat


@pytest.mark.parametrize(
    "status,expected_calls",
    [(401, 1), (402, 1), (403, 1), (400, 1), (429, 3), (500, 3), (503, 3)],
)
def test_retry_policy_respects_provider_class(monkeypatch, status, expected_calls):
    monkeypatch.setattr("src.domain.services.retry_policy.time.sleep", lambda *_args, **_kwargs: None)
    calls = {"n": 0}

    def func():
        calls["n"] += 1
        if status == 429:
            raise GeminiRateLimitError("gemini-3.5-flash-lite")
        raise GeminiRequestError("gemini-3.5-flash-lite", status)

    policy = RetryPolicy(3, backoff_base=0.5)
    with pytest.raises(GeminiRequestError):
        policy.run(func)
    assert calls["n"] == expected_calls


def test_retry_policy_retries_timeout_and_unknown(monkeypatch):
    monkeypatch.setattr("src.domain.services.retry_policy.time.sleep", lambda *_args, **_kwargs: None)

    timeouts = {"n": 0}

    def time_out():
        timeouts["n"] += 1
        raise requests.Timeout("timed out")

    with pytest.raises(requests.Timeout):
        RetryPolicy(3, backoff_base=0).run(time_out)
    assert timeouts["n"] == 3

    unknown = {"n": 0}

    def boom():
        unknown["n"] += 1
        raise ValueError("parse")

    with pytest.raises(ValueError):
        RetryPolicy(2, backoff_base=0).run(boom)
    assert unknown["n"] == 2


def test_neon_and_line_classes():
    import psycopg.errors as errors

    # InvalidPassword は OperationalError の子なので、認証側を先に見る
    denied = classify_provider_failure(errors.InvalidPassword("bad password"))
    assert denied.dependency == "neon"
    assert denied.error_class == "non_retryable"
    assert denied.retryable is False
    down = classify_provider_failure(errors.OperationalError("connection reset"))
    assert down.dependency == "neon"
    assert down.error_class == "transient"
    assert down.retryable is True
    assert should_retry_provider_error(errors.ConnectionTimeout("slow")) is True

    line_auth = LineApiError("LINE reply failed with status 401", status_code=401)
    line_busy = LineApiError("LINE reply failed with status 429", status_code=429)
    line_down = LineApiError("LINE reply failed with status 500", status_code=500)
    assert classify_provider_failure(line_auth).error_class == "non_retryable"
    assert classify_provider_failure(line_busy).error_class == "transient"
    assert classify_provider_failure(line_down).retryable is True
    assert should_retry_provider_error(line_auth) is False
    assert should_retry_provider_error(line_down) is True


def test_line_reply_records_status_once(capsys):
    class _Session:
        def post(self, url, json=None, timeout=None):  # noqa: A002
            return _StatusResponse(401)

    adapter = LineApiAdapter("token")
    adapter._session = _Session()
    with pytest.raises(LineApiError) as exc_info:
        adapter.reply_text("reply-token", "hello")
    assert exc_info.value.status_code == 401
    assert "token" not in str(exc_info.value)
    payloads = _failures(capsys.readouterr().out)
    assert len(payloads) == 1
    assert payloads[0]["dependency"] == "line"
    assert payloads[0]["error_class"] == "non_retryable"
    assert payloads[0]["status"] == 401


def test_line_timeout_is_transient(capsys):
    class _Session:
        def post(self, url, json=None, timeout=None):  # noqa: A002
            raise requests.ConnectionError("reset")

    adapter = LineApiAdapter("token")
    adapter._session = _Session()
    with pytest.raises(requests.ConnectionError):
        adapter.reply_text("reply-token", "hello")
    payloads = _failures(capsys.readouterr().out)
    assert len(payloads) == 1
    assert payloads[0]["dependency"] == "line"
    assert payloads[0]["error_class"] == "transient"
    assert payloads[0]["status"] is None


def test_language_preference_skips_second_attempt_on_402(monkeypatch, capsys):
    monkeypatch.setattr("src.infra.language_pref_client.time.sleep", lambda *_args, **_kwargs: None)
    session = _StatusSession(402)
    adapter = LanguagePreferenceAdapter("api-key", "gemini-3.5-flash-lite", session=session)
    with pytest.raises(GeminiRequestError):
        adapter.analyze("日本語にして")
    assert session.calls == 1
    payloads = _failures(capsys.readouterr().out)
    assert len(payloads) == 1
    assert payloads[0]["status"] == 402
    assert "api-key" not in json.dumps(payloads[0])


def test_language_preference_retries_500(monkeypatch, capsys):
    monkeypatch.setattr("src.infra.language_pref_client.time.sleep", lambda *_args, **_kwargs: None)
    session = _StatusSession(500)
    adapter = LanguagePreferenceAdapter("api-key", "gemini-3.5-flash-lite", session=session)
    with pytest.raises(GeminiRequestError):
        adapter.analyze("日本語にして")
    assert session.calls == 2
    assert len(_failures(capsys.readouterr().out)) == 2


def _handler_with_retry(retry: int) -> MessageHandler:
    handler = MessageHandler.__new__(MessageHandler)
    handler._translation_retry = retry
    return handler


@pytest.mark.parametrize(
    "status,expected_calls",
    [(402, 1), (401, 1), (403, 1), (429, 4), (500, 4)],
)
def test_message_handler_retry_policy(monkeypatch, status, expected_calls):
    monkeypatch.setattr("src.app.handlers.message_handler.time.sleep", lambda *_args, **_kwargs: None)
    handler = _handler_with_retry(4)
    calls = {"n": 0}

    def func():
        calls["n"] += 1
        if status == 429:
            raise GeminiRateLimitError("gemini-3.5-flash-lite")
        raise GeminiRequestError("gemini-3.5-flash-lite", status)

    with pytest.raises(GeminiRequestError):
        handler._run_with_retry("Gemini translation", func)
    assert calls["n"] == expected_calls


def test_non_retryable_outage_stays_quiet(capsys):
    handler = MessageHandler.__new__(MessageHandler)
    event = models.MessageEvent(
        event_type="message",
        reply_token="reply",
        group_id="G",
        user_id="U",
        sender_type="group",
        timestamp=1_700_000_000_000,
        text="hello",
    )
    start = SimpleNamespace(legacy=True, sender_name="A", messages_future=None)
    handler._begin_group_message = lambda _event: start

    def boom(_event, _start):
        raise GeminiRequestError("gemini-3.5-flash-lite", 402)

    handler._process_group_message = boom
    replies: list[str] = []
    handler._reply_text = lambda *_args, **_kwargs: replies.append("reply")
    handler._send_rate_limit_notice = lambda *_args, **_kwargs: replies.append("rate")
    handler._drain_optional_future = lambda *_args, **_kwargs: None
    handler._record_message = lambda *_args, **_kwargs: None

    handler.handle(event)

    assert replies == []
    payloads = _failures(capsys.readouterr().out)
    assert len(payloads) == 1
    assert payloads[0]["error_class"] == "non_retryable"
    assert payloads[0]["status"] == 402
    assert payloads[0]["dependency"] == "gemini"


def test_rate_limit_still_notifies_once_per_group(monkeypatch):
    monkeypatch.setattr("src.app.handlers.message_handler.time.sleep", lambda *_args, **_kwargs: None)
    handler = MessageHandler.__new__(MessageHandler)
    event = models.MessageEvent(
        event_type="message",
        reply_token="reply",
        group_id="G-rate-limit-notice",
        user_id="U",
        sender_type="group",
        timestamp=1_700_000_000_000,
        text="hello",
    )
    start = SimpleNamespace(legacy=True, sender_name="A", messages_future=None)
    handler._begin_group_message = lambda _event: start

    def boom(_event, _start):
        raise GeminiRateLimitError("gemini-3.5-flash-lite")

    handler._process_group_message = boom
    replies: list[str] = []

    def reply(_event, text, **_kwargs):
        replies.append(text)
        return True

    handler._reply_text = reply
    handler._send_rate_limit_notice = MessageHandler._send_rate_limit_notice.__get__(handler, MessageHandler)
    handler._drain_optional_future = lambda *_args, **_kwargs: None
    handler._record_message = lambda *_args, **_kwargs: None
    _last_rate_limit_message.pop(event.group_id, None)
    try:
        handler.handle(event)
        handler.handle(event)
        assert replies == [RATE_LIMIT_MESSAGE]
    finally:
        _last_rate_limit_message.pop(event.group_id, None)


@pytest.mark.parametrize("label,text", TRANSLATION_SAMPLES)
def test_translation_success_path_still_translates(label, text, capsys):
    detector = LanguageDetectionService()
    translator = _FakeTranslator()
    service = TranslationService(translator, detector)
    result = service.translate(
        sender_name="A",
        message_text=text,
        timestamp=datetime(2026, 10, 26, tzinfo=timezone.utc),
        context_messages=[],
        candidate_languages=["ja", "en"],
    )
    if not detector.has_translatable_text(text):
        assert result == []
        assert translator.calls == []
    else:
        assert translator.calls, label
        assert translator.calls[0].message_text == text
        assert result
        assert result[0].text.startswith("translated:")
    assert _failures(capsys.readouterr().out) == []
    if label == "mixed":
        assert detector.is_mixed_language(text, ["ja", "en"]) is True
        assert set(translator.calls[0].candidate_languages) == {"ja", "en"}
    if label == "long":
        assert len(translator.calls[0].message_text) > 800


@pytest.mark.parametrize("label,text", TRANSLATION_SAMPLES)
@pytest.mark.parametrize("status,error_class,attempts", FAILURE_CASES)
def test_translation_failures_are_classified_for_each_input(
    monkeypatch,
    capsys,
    label,
    text,
    status,
    error_class,
    attempts,
):
    monkeypatch.setattr("src.domain.services.retry_policy.time.sleep", lambda *_args, **_kwargs: None)
    detector = LanguageDetectionService()
    session = _StatusSession(status)
    adapter = GeminiTranslationAdapter(api_key="api-key", model="gemini-3.5-flash-lite", timeout_seconds=5)
    adapter._session = session
    service = TranslationService(adapter, detector)

    def invoke():
        return service.translate(
            sender_name="A",
            message_text=text,
            timestamp=datetime(2026, 10, 26, tzinfo=timezone.utc),
            context_messages=[],
            candidate_languages=["ja", "en"],
        )

    if not detector.has_translatable_text(text):
        assert invoke() == []
        assert session.calls == 0
        assert _failures(capsys.readouterr().out) == []
        return

    policy = RetryPolicy(3, backoff_base=0.5)
    expected = requests.Timeout if status is None else GeminiRequestError
    with pytest.raises(expected):
        policy.run(invoke)

    assert session.calls == attempts, label
    payloads = _failures(capsys.readouterr().out)
    assert len(payloads) == attempts
    assert {item["error_class"] for item in payloads} == {error_class}
    assert {item["status"] for item in payloads} == {status}
    assert {item["dependency"] for item in payloads} == {"gemini"}
    assert all("api-key" not in json.dumps(item) for item in payloads)


def test_alarm_template_matches_metric_contract():
    text = (ROOT / "template.yaml").read_text(encoding="utf-8")
    required = [
        "AlertEmail:",
        "AlertWebhookUrl:",
        "ProviderFailureAlarmThreshold:",
        "Default: 5",
        "HasAlertEmail:",
        "HasAlertWebhook:",
        "Protocol: email",
        "Protocol: https",
        "Namespace: KOTORI/Translation",
        "MetricName: ProviderFailure",
        "Value: provider_failure",
        "Value: non_retryable",
        "Period: 900",
        "Period: 60",
        "WebhookLambdaErrorsAlarm:",
        "MetricName: Errors",
        "FunctionName",
        "OperatorAlertTopic:",
        "TreatMissingData: notBreaching",
    ]
    missing = [item for item in required if item not in text]
    assert missing == []
    # 通知先の既定は空。未設定のデプロイを止めない。
    assert "AlertEmail:" in text
    email_block = text.split("AlertEmail:", 1)[1].split("AlertWebhookUrl:", 1)[0]
    assert 'Default: ""' in email_block
    webhook_block = text.split("AlertWebhookUrl:", 1)[1].split("ProviderFailureAlarmThreshold:", 1)[0]
    assert 'Default: ""' in webhook_block
