"""停止状態の遷移。上限・月替わり・有料化・課金・送信失敗を同じ表で見る。"""

import types
from datetime import datetime, timezone

import pytest
import requests

from src.domain.models import TranslationResult
from src.domain.services.interface_translation_service import InterfaceTranslationService
from src.domain.services.language_detection_service import LanguageDetectionService
from src.domain.services.quota_pause import (
    BILLING_PAUSE,
    QUOTA_PAUSE,
    NoticeClaim,
    PauseState,
    pause_for_billing,
    recover_pause,
    reset_pause_for_claim,
)
from src.domain.services.translation_service import TranslationService
from tests.test_quota_limits import (
    ProQuotaRepo,
    RecordingLineClient,
    RecordingTranslationService,
    _build_event,
    _build_handler,
)
from tests.test_quota_notice_flow import (
    _CapturingLine,
    _FailingLine,
    _MutableLanguageRepo,
    _next_month,
    _uris,
    _with_checkout,
)
from tests.test_quota_pause_reevaluation import _SubscriptionRepo, _standard_subscription
from tests.test_stripe_webhook_billing_owner import _import_module


def _active(reason=None):
    return PauseState(enabled=True, reason=reason)


def _stopped(reason):
    return PauseState(enabled=False, reason=reason)


# 翻訳中 / 手動 / quota / 案内待ち / billing に、課金失敗と復旧を掛けた表。
_PAUSE_ROWS = [
    ("active", _active(), "billing_failed", 0, 50, False, BILLING_PAUSE),
    ("manual", _stopped(None), "billing_failed", 0, 50, False, None),
    ("quota", _stopped(QUOTA_PAUSE), "billing_failed", 50, 50, False, QUOTA_PAUSE),
    ("notice_pending", _active(QUOTA_PAUSE), "billing_failed", 0, 50, False, QUOTA_PAUSE),
    ("billing", _stopped(BILLING_PAUSE), "billing_failed", 0, 50, False, BILLING_PAUSE),
    ("recover_billing", _stopped(BILLING_PAUSE), "recover", 0, 50, True, None),
    ("recover_manual", _stopped(None), "recover", 0, 50, False, None),
    ("recover_quota_under_paid", _stopped(QUOTA_PAUSE), "recover", 50, 4000, True, None),
    ("recover_quota_still_over", _stopped(QUOTA_PAUSE), "recover", 50, 50, False, QUOTA_PAUSE),
    ("recover_notice_pending", _active(QUOTA_PAUSE), "recover", 0, 50, True, None),
]


@pytest.mark.parametrize(
    ("claim", "enabled", "reason"),
    [
        (NoticeClaim.ACQUIRED, False, QUOTA_PAUSE),
        (NoticeClaim.ALREADY, True, None),
        (NoticeClaim.FAILED, True, QUOTA_PAUSE),
    ],
)
def test_reset_claim_keeps_a_quota_reason_until_the_notice_is_sent(claim, enabled, reason):
    assert reset_pause_for_claim(claim) == PauseState(enabled=enabled, reason=reason)


@pytest.mark.parametrize(
    ("name", "state", "event", "usage", "limit", "enabled", "reason"),
    _PAUSE_ROWS,
    ids=[row[0] for row in _PAUSE_ROWS],
)
def test_pause_state_table(name, state, event, usage, limit, enabled, reason):
    del name
    if event == "billing_failed":
        nxt = pause_for_billing(state)
    else:
        nxt = recover_pause(state, usage=usage, limit=limit)
    assert nxt == PauseState(enabled=enabled, reason=reason)


class _TimeoutTranslation(RecordingTranslationService):
    def translate(self, *, sender_name, message_text, timestamp, context_messages, candidate_languages):
        raise requests.Timeout("gemini")


def _free_handler(repo, line, translation=None):
    return _with_checkout(_build_handler(repo, line, translation or RecordingTranslationService()))


def _run(handler, text, *, enabled=True):
    handler._handle_translation_flow(_build_event(text), sender_name="user", translation_enabled=enabled)


def test_usage_grows_from_49_to_50_and_51_stays_quiet():
    line = _CapturingLine()
    repo = ProQuotaRepo(initial_usage=48, paid=False)
    repo.approaching_claimed = True
    handler = _free_handler(repo, line)

    _run(handler, "forty-nine")
    assert repo.usage == 49
    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert "Translation stopped" not in "\n".join(line.sent_texts)

    line.sent_texts.clear()
    _run(handler, "fiftieth")
    assert repo.usage == 50
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"
    assert repo.notice_plan == "free"
    assert "Translation stopped" in "\n".join(line.sent_texts)
    assert _next_month(datetime.now(timezone.utc)) in "\n".join(line.sent_texts)

    line.sent_texts.clear()
    _run(handler, "fifty-first")
    assert line.sent_texts == []
    assert repo.usage == 50
    assert repo.paused_reason == "quota"
    assert repo.notice_plan == "free"


def test_month_rollover_language_change_sends_one_reset_notice():
    line = RecordingLineClient()
    repo = _MutableLanguageRepo(
        initial_usage=0,
        paid=False,
        translation_enabled=False,
        paused_reason="quota",
        languages=["en", "ja"],
    )
    handler = _free_handler(repo, line)

    handler._handle_command(_build_event("add Thai"), "add Thai")

    assert "th" in {code.lower() for code in repo.languages}
    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert "\n".join(line.sent_texts).count("has reset") == 1

    line.sent_texts.clear()
    _run(handler, "hello")
    assert "has reset" not in "\n".join(line.sent_texts)


def test_limit_notice_build_failure_keeps_the_quota_pause_and_retries():
    """案内の組み立てより先に quota 停止を残し、通知済みにはしない。"""

    repo = ProQuotaRepo(initial_usage=49, paid=False)
    handler = _free_handler(repo, RecordingLineClient())

    def boom(*_args, **_kwargs):
        assert repo.translation_enabled is False
        assert repo.paused_reason == "quota"
        raise RuntimeError("gemini down")

    handler._build_limit_reached_notice_text = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="gemini down"):
        _run(handler, "fiftieth")

    assert repo.usage == 50
    assert repo.notice_plan is None
    assert repo.paused_reason == "quota"

    line = _CapturingLine()
    retry = _free_handler(repo, line)
    _run(retry, "fifty-first")
    assert repo.notice_plan == "free"
    assert "Translation stopped" in "\n".join(line.sent_texts)


def test_limit_notice_send_failure_does_not_record_the_plan():
    repo = ProQuotaRepo(initial_usage=49, paid=False)
    handler = _free_handler(repo, _FailingLine())

    with pytest.raises(RuntimeError, match="line down"):
        _run(handler, "fiftieth")

    assert repo.usage == 50
    assert repo.notice_plan is None
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"

    line = _CapturingLine()
    retry = _free_handler(repo, line)
    _run(retry, "again")
    assert repo.notice_plan == "free"
    assert "Translation stopped" in "\n".join(line.sent_texts)
    assert repo.paused_reason == "quota"


def test_reset_claim_db_error_keeps_the_quota_reason_and_retries(monkeypatch):
    """claim の DB 例外は通知済みではない。停止理由を残し、次の投稿で案内する。"""

    monkeypatch.setattr("src.app.handlers.message_handler.time.sleep", lambda *_args, **_kwargs: None)
    repo = _MutableLanguageRepo(
        initial_usage=0,
        paid=False,
        translation_enabled=False,
        paused_reason="quota",
        languages=["en", "ja"],
    )
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    handler = _free_handler(repo, line, translation)

    def db_down(*_args, **_kwargs):
        raise RuntimeError("db down")

    repo.claim_quota_reset_notice = db_down

    _run(handler, "hello", enabled=False)

    assert translation.calls == 1
    assert repo.usage == 1
    assert repo.translation_enabled is True
    assert repo.paused_reason == "quota"
    assert repo.reset_notices == set()
    assert "has reset" not in "\n".join(line.sent_texts)

    def claim(_group_id, period_key):
        if period_key in repo.reset_notices:
            return False
        repo.reset_notices.add(period_key)
        return True

    repo.claim_quota_reset_notice = claim
    retry_line = RecordingLineClient()
    retry = _free_handler(repo, retry_line, translation)
    _run(retry, "again", enabled=True)

    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert repo.reset_notices
    assert "has reset" in "\n".join(retry_line.sent_texts)


def test_reset_claim_rolls_back_when_translation_times_out():
    repo = _MutableLanguageRepo(
        initial_usage=0,
        paid=False,
        translation_enabled=False,
        paused_reason="quota",
        languages=["en", "ja"],
    )
    handler = _free_handler(repo, RecordingLineClient(), _TimeoutTranslation())

    with pytest.raises(requests.Timeout):
        _run(handler, "hello", enabled=False)

    assert repo.usage == 0
    assert repo.reset_notices == set()
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"

    line = RecordingLineClient()
    retry = _free_handler(repo, line)
    _run(retry, "hello", enabled=False)
    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert repo.reset_notices
    assert "has reset" in "\n".join(line.sent_texts)


def test_reset_claim_rolls_back_when_line_send_fails():
    repo = _MutableLanguageRepo(
        initial_usage=0,
        paid=False,
        translation_enabled=False,
        paused_reason="quota",
        languages=["en", "ja"],
    )
    handler = _free_handler(repo, _FailingLine())

    with pytest.raises(RuntimeError, match="line down"):
        _run(handler, "hello", enabled=False)

    assert repo.reset_notices == set()
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"

    line = RecordingLineClient()
    retry = _free_handler(repo, line)
    _run(retry, "hello", enabled=False)
    assert "has reset" in "\n".join(line.sent_texts)
    assert repo.paused_reason is None


def test_language_change_reset_rolls_back_when_line_send_fails():
    repo = _MutableLanguageRepo(
        initial_usage=0,
        paid=False,
        translation_enabled=False,
        paused_reason="quota",
        languages=["en", "ja"],
    )
    handler = _free_handler(repo, _FailingLine())

    with pytest.raises(RuntimeError, match="line down"):
        handler._handle_command(_build_event("add Thai"), "add Thai")

    assert "th" in {code.lower() for code in repo.languages}
    assert repo.reset_notices == set()
    assert repo.paused_reason == "quota"
    assert repo.translation_enabled is False

    line = RecordingLineClient()
    retry = _free_handler(repo, line)
    retry._handle_command(_build_event("add Thai"), "add Thai")
    assert "\n".join(line.sent_texts).count("has reset") == 1
    assert repo.paused_reason is None


def _bind_catalog(monkeypatch, module):
    # 確認通知はテスト側で数える。価格表だけ差し替える。
    from src.domain.services.plan_policy import BILLING_MONTH, STANDARD_PLAN, PriceDefinition

    monkeypatch.setattr(
        module,
        "price_catalog",
        types.SimpleNamespace(
            resolve_price=lambda _price_id: PriceDefinition("standard_monthly", STANDARD_PLAN, BILLING_MONTH)
        ),
    )


def _checkout(module, subscription):
    module.stripe.checkout = types.SimpleNamespace(
        Session=types.SimpleNamespace(
            retrieve=lambda _session_id, expand=None: {
                "id": "cs_1",
                "mode": "subscription",
                "metadata": {"group_id": "gid"},
                "subscription": subscription,
            }
        )
    )
    module._handle_checkout_session_completed(
        {"id": "cs_1"},
        int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()),
    )


def test_upgrade_while_quota_paused_clears_only_the_quota_pause(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason="quota", enabled=False)
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    pushed = []
    monkeypatch.setattr(module, "_push_payment_confirmation", pushed.append)
    _bind_catalog(monkeypatch, module)
    _checkout(module, _standard_subscription())

    assert repo.enabled is True
    assert repo.paused_reason is None
    assert pushed == ["gid"]


def test_payment_failure_then_success_clears_billing_pause_only(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason=None, enabled=True)
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    pushed = []
    monkeypatch.setattr(module, "_push_payment_confirmation", pushed.append)
    _bind_catalog(monkeypatch, module)
    subscription = _standard_subscription()
    module.stripe.Subscription = types.SimpleNamespace(retrieve=lambda _subscription_id: subscription)
    created = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())

    module._handle_payment_failed({"id": "in_1", "subscription": "sub_1"}, created)
    assert repo.enabled is False
    assert repo.paused_reason == "billing"
    assert pushed == []

    module._handle_payment_succeeded({"id": "in_2", "subscription": "sub_1"}, created)
    assert repo.enabled is True
    assert repo.paused_reason is None
    assert pushed == ["gid"]


def test_payment_recovery_does_not_clear_a_manual_pause(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason=None, enabled=False)
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    pushed = []
    monkeypatch.setattr(module, "_push_payment_confirmation", pushed.append)
    _bind_catalog(monkeypatch, module)
    subscription = _standard_subscription()
    module.stripe.Subscription = types.SimpleNamespace(retrieve=lambda _subscription_id: subscription)
    created = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())

    module._handle_payment_failed({"id": "in_1", "subscription": "sub_1"}, created)
    module._handle_payment_succeeded({"id": "in_2", "subscription": "sub_1"}, created)

    assert repo.enabled is False
    assert repo.paused_reason is None
    assert pushed == []


def test_cancel_then_resubscribe_clears_the_billing_pause(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason=None, enabled=True)
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    pushed = []
    monkeypatch.setattr(module, "_push_payment_confirmation", pushed.append)
    _bind_catalog(monkeypatch, module)
    subscription = _standard_subscription()
    created = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())

    module._handle_subscription_deleted(subscription, created)
    assert repo.enabled is False
    assert repo.paused_reason == "billing"

    _checkout(module, subscription)
    assert repo.enabled is True
    assert repo.paused_reason is None
    assert pushed == ["gid"]


def test_payment_failure_keeps_a_quota_pause_until_the_paid_plan_fits(monkeypatch):
    module = _import_module(monkeypatch)
    repo = _SubscriptionRepo(paused_reason="quota", enabled=False)
    repo.usage = 50
    monkeypatch.setattr(module, "_get_repo", lambda: repo)
    pushed = []
    monkeypatch.setattr(module, "_push_payment_confirmation", pushed.append)
    _bind_catalog(monkeypatch, module)
    subscription = _standard_subscription()
    module.stripe.Subscription = types.SimpleNamespace(retrieve=lambda _subscription_id: subscription)
    created = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())

    module._handle_payment_failed({"id": "in_1", "subscription": "sub_1"}, created)
    assert repo.enabled is False
    assert repo.paused_reason == "quota"

    module._handle_payment_succeeded({"id": "in_2", "subscription": "sub_1"}, created)
    assert repo.enabled is True
    assert repo.paused_reason is None
    assert pushed == ["gid"]


class _ScriptPort:
    def __init__(self, replies):
        self.replies = replies
        self.requests = []

    def translate(self, request):
        self.requests.append(request)
        langs = list(request.candidate_languages)
        reply = self.replies
        if callable(reply):
            return reply(request)
        return [
            TranslationResult(lang=lang, text=reply.get(lang, f"[{lang}] {request.message_text}"))
            for lang in langs
        ]


@pytest.mark.parametrize(
    ("text", "languages", "translated"),
    [
        ("Hello from the group chat today", ["en", "ja"], True),
        ("Hello from the group chat today", ["en", "th"], True),
        ("Hello from the group chat today", ["en", "zh"], True),
        ("Hello from the group chat today", ["en", "ko"], True),
        ("Hello from the group chat today", ["en", "vi"], True),
        ("Hello from the group chat today", ["en", "id"], True),
        ("Hello from the group chat today", ["en", "es"], True),
        ("Hello from the group chat today", ["en", "xx"], True),
        ("Hello from the group today.\nこんにちは、元気ですか", ["en", "ja"], True),
        ("😀😀😀", ["en", "ja"], False),
        ("12345", ["en", "ja"], False),
        ("", ["en", "ja"], False),
        (" \n\t ", ["en", "ja"], False),
    ],
)
def test_translation_path_languages_and_unexpected_text(text, languages, translated):
    port = _ScriptPort({})
    repo = _MutableLanguageRepo(initial_usage=4, paid=False, languages=languages)
    line = RecordingLineClient()
    service = TranslationService(port, LanguageDetectionService())
    handler = _free_handler(repo, line, service)

    _run(handler, text)

    if translated:
        assert port.requests
        requested = {lang.lower() for request in port.requests for lang in request.candidate_languages}
        assert languages[-1].lower() in requested
        if "\n" in text:
            assert {lang.lower() for lang in languages} <= requested
        assert repo.usage == 5
        assert repo.paused_reason is None
        assert f"[{languages[-1]}]" in "\n".join(line.sent_texts)
    else:
        assert port.requests == []
        assert repo.usage == 4
        assert repo.translation_enabled is True
        assert line.sent_texts == []


def test_notice_path_keeps_numbers_for_many_languages_and_falls_back():
    languages = ["en", "ja", "th", "zh", "ko", "vi", "id", "es", "xx", "blank", "emo"]

    def reply(request):
        results = []
        for lang in request.candidate_languages:
            if lang == "xx":
                results.append(TranslationResult(lang=lang, text="no digits"))
            elif lang == "blank":
                results.append(TranslationResult(lang=lang, text=" \n "))
            elif lang == "emo":
                results.append(TranslationResult(lang=lang, text="😀 only"))
            else:
                results.append(TranslationResult(lang=lang, text=f"[{lang}] {request.message_text}"))
        return results

    class _Repo:
        def fetch_group_languages(self, _group_id):
            return list(languages)

    from src.app.handlers.message_handler import MessageHandler

    handler = MessageHandler(
        line_client=_Repo(),
        translation_service=_Repo(),
        interface_translation=InterfaceTranslationService(_ScriptPort(reply)),
        language_detector=_Repo(),
        language_pref_service=_Repo(),
        command_router=_Repo(),
        repo=_Repo(),
        max_context_messages=1,
        max_group_languages=12,
        translation_retry=1,
        bot_mention_name="bot",
        free_quota_per_month=50,
    )
    handler._subscription_service.create_checkout_url = lambda _gid: "https://short.example.com/cs"  # type: ignore[attr-defined]

    notice, url = handler._build_limit_reached_notice_text(
        "group1",
        plan_key="free",
        limit=50,
        period_key="2026-10-01",
    )

    assert url == "https://short.example.com/cs"
    assert "2026-11-01" in notice
    assert "50" in notice
    for lang in ("ja", "th", "zh", "ko", "vi", "id", "es"):
        assert f"[{lang}]" in notice
    assert "[xx]" not in notice
    assert "[blank]" not in notice
    assert "[emo]" not in notice
    # en と、数字を落とした訳の代わり。未登録コードも英語原文。
    assert notice.count("Translation stopped") >= 4


class _MarkingPort:
    """英語原文を残したまま、言語ごとの印を付ける。"""

    def translate(self, request):
        return [
            TranslationResult(lang=lang, text=f"[{lang}] {request.message_text}")
            for lang in request.candidate_languages
        ]


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
        ["zz"],
        ["ja", "th", "my"],
    ],
)
def test_limit_notice_for_group_languages_is_not_empty_and_has_plan_link(languages):
    """案内は英語原文を設定言語へ訳す。空にならず、課金リンクを別メッセージに付ける。"""

    repo = _MutableLanguageRepo(initial_usage=49, paid=False, languages=languages)
    line = _CapturingLine()
    handler = _with_checkout(_build_handler(repo, line, RecordingTranslationService()))
    handler._interface_translation = InterfaceTranslationService(_MarkingPort())

    _run(handler, "fiftieth")

    notice = "\n".join(line.sent_texts).strip()
    assert notice
    assert "Translation stopped" in notice or any(f"[{lang}]" in notice for lang in languages)
    assert _uris(line.payloads) == ["https://short.example.com/cs"]
    assert repo.paused_reason == "quota"
    assert repo.translation_enabled is False


@pytest.mark.parametrize("text", ["😀😀😀", "12345", " \n\t ", "@bot"])
def test_inputs_after_the_quota_cap_do_not_crash(text):
    repo = ProQuotaRepo(
        initial_usage=50,
        paid=False,
        translation_enabled=False,
        paused_reason="quota",
        notice_plan="free",
    )
    line = _CapturingLine()
    handler = _with_checkout(_build_handler(repo, line, RecordingTranslationService()))
    event = _build_event(text)

    handler._process_group_message(event, handler._begin_group_message(event))

    assert repo.paused_reason == "quota"
    assert repo.translation_enabled is False
    assert repo.usage == 50
