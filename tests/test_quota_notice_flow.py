"""無料枠の上限案内。49/50/51、事前案内、停止中メンション、リセット、多言語。"""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.domain.services.quota_notice import (
    approaching_usage_threshold,
    build_plan_link_message,
    plan_catalog_text,
)
from tests.test_quota_limits import (
    ProQuotaRepo,
    RecordingLineClient,
    RecordingTranslationService,
    _build_event,
    _build_handler,
)


class _CapturingLine(RecordingLineClient):
    def __init__(self):
        super().__init__()
        self.payloads = []

    def reply_text(self, reply_token, text):
        self.payloads.append([{"type": "text", "text": text}])
        super().reply_text(reply_token, text)

    def reply_messages(self, reply_token, messages):
        self.payloads.append(messages)
        super().reply_messages(reply_token, messages)


class _BoomRouter:
    def decide(self, _text):
        raise AssertionError("quota pause should answer before the command router")


class _ActionRouter:
    def __init__(self, action: str):
        self._action = action

    def decide(self, _text):
        return models.CommandDecision(action=self._action, instruction_language="en", ack_text="")


class _ResumeRouter:
    def decide(self, _text):
        return models.CommandDecision(action="resume", instruction_language="en", ack_text="")


class _MenuRouter:
    def decide(self, _text):
        return models.CommandDecision(action="subscription_menu", instruction_language="en", ack_text="")


def _next_month(now: datetime) -> str:
    if now.month == 12:
        return f"{now.year + 1}-01-01"
    return f"{now.year:04d}-{now.month + 1:02d}-01"


def _with_checkout(handler, line=None):
    handler._subscription_service.create_checkout_url = lambda _gid: "https://short.example.com/cs"  # type: ignore[attr-defined]
    return handler


def _uris(payloads) -> list[str]:
    found = []
    for messages in payloads:
        for message in messages:
            actions = (message.get("template") or {}).get("actions") or []
            for action in actions:
                if action.get("uri"):
                    found.append(action["uri"])
    return found


def test_approaching_threshold_is_forty_for_free_fifty():
    assert approaching_usage_threshold(50) == 40
    assert approaching_usage_threshold(4000) == 3200
    assert approaching_usage_threshold(4) is None


def test_plan_catalog_uses_configured_quotas_without_currency():
    text = plan_catalog_text(free_quota=50, standard_quota=4000, pro_quota=40000)
    assert "Free 50/month (3 languages)" in text
    assert "Standard 4,000/month (3 languages)" in text
    assert "Pro 40,000/month (5 languages)" in text
    assert "¥" not in text
    assert "$" not in text
    assert "円" not in text


def test_plan_link_is_a_button_and_long_urls_stay_text():
    button = build_plan_link_message("https://short.example.com/cs")
    assert button["type"] == "template"
    assert button["template"]["actions"][0]["uri"] == "https://short.example.com/cs"
    assert button["template"]["actions"][0]["label"] == "See plans"

    long_url = "https://example.com/" + ("a" * 1200)
    fallback = build_plan_link_message(long_url)
    assert fallback["type"] == "text"
    assert fallback["text"].startswith("https://example.com/")


def test_reset_date_uses_period_end_and_clamps_short_months():
    handler = _build_handler(ProQuotaRepo(0, paid=False), RecordingLineClient(), RecordingTranslationService())
    assert handler._resolve_quota_reset_date(period_key="2026-01-31", period_end=None) == "2026-02-28"
    assert handler._resolve_quota_reset_date(period_key="2026-12-01", period_end=None) == "2027-01-01"
    period_end = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    assert handler._resolve_quota_reset_date(period_key="2026-01-01", period_end=period_end) == "2026-03-02"
    assert handler._resolve_quota_reset_date(
        period_key="2026-01-01",
        period_end=period_end,
        plan_key="pro",
    ) == "2026-03-02"
    # Free は旧有料契約の終了日を使わず、暦月キーの翌月1日
    assert handler._resolve_quota_reset_date(
        period_key="2026-10-01",
        period_end=period_end,
        plan_key="free",
    ) == "2026-11-01"


class _MutableLanguageRepo(ProQuotaRepo):
    def __init__(self, *args, languages=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.languages = list(languages or ["en"])

    def fetch_group_languages(self, _group_id):
        return list(self.languages)

    def fetch_translation_runtime_state(self, group_id):
        state = super().fetch_translation_runtime_state(group_id)
        return replace(state, group_languages=list(self.languages))

    def add_group_languages(self, _group_id, languages):
        present = {code.lower() for code in self.languages}
        for code, _name in languages:
            if code and code.lower() not in present:
                self.languages.append(code)
                present.add(code.lower())

    def remove_group_languages(self, _group_id, codes):
        drop = {code.lower() for code in codes if code}
        self.languages = [code for code in self.languages if code.lower() not in drop]

    def replace_group_languages(self, _group_id, languages):
        self.languages = [code for code, _name in languages if code]


def _assert_quota_pause(repo: ProQuotaRepo) -> None:
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"


def test_free_notices_use_next_month_when_canceled_period_end_remains():
    """解約後に残った current_period_end を、無料枠の再開日に使わない。"""

    stale = datetime(2024, 3, 17, tzinfo=timezone.utc)
    next_month = _next_month(datetime.now(timezone.utc))
    line = _CapturingLine()
    repo = ProQuotaRepo(
        initial_usage=39,
        paid=False,
        subscription_status="canceled",
        period_end=stale,
        entitlement_plan="standard",
    )
    handler = _with_checkout(_build_handler(repo, line, RecordingTranslationService()))

    handler._handle_translation_flow(_build_event("near"), sender_name="user", translation_enabled=True)

    approaching = "\n".join(line.sent_texts)
    assert "40 of 50" in approaching
    assert next_month in approaching
    assert "2024-03-17" not in approaching

    repo.usage = 50
    repo.translation_enabled = False
    repo.paused_reason = "quota"
    line.sent_texts.clear()
    handler._command_router = _ResumeRouter()
    handler._handle_command(_build_event("resume"), "resume")
    reminder = "\n".join(line.sent_texts)
    assert "still paused" in reminder
    assert next_month in reminder
    assert "2024-03-17" not in reminder

    line.sent_texts.clear()
    handler._handle_command(_build_event("resume"), "resume")
    short = "\n".join(line.sent_texts)
    assert "stays paused" in short
    assert next_month in short
    assert "2024-03-17" not in short

    repo.usage = 0
    line.sent_texts.clear()
    handler._handle_translation_flow(_build_event("new month"), sender_name="user", translation_enabled=False)

    reset = "\n".join(line.sent_texts)
    assert "has reset" in reset
    assert next_month in reset
    assert "2024-03-17" not in reset
    assert repo.translation_enabled is True
    assert repo.paused_reason is None


def test_language_changes_during_quota_pause_keep_the_reason():
    """追加・削除・置換と unknown は、上限停止の理由を消さない。"""

    line = RecordingLineClient()
    repo = _MutableLanguageRepo(
        initial_usage=50,
        paid=False,
        notice_plan="free",
        translation_enabled=False,
        paused_reason="quota",
        languages=["en", "ja"],
    )
    handler = _with_checkout(_build_handler(repo, line, RecordingTranslationService()))

    handler._handle_command(_build_event("add Thai"), "add Thai")
    assert "th" in {code.lower() for code in repo.languages}
    _assert_quota_pause(repo)

    handler._handle_command(_build_event("remove Japanese"), "remove Japanese")
    assert "ja" not in {code.lower() for code in repo.languages}
    _assert_quota_pause(repo)

    handler._handle_command(_build_event("Japanese"), "Japanese")
    assert [code.lower() for code in repo.languages] == ["ja"]
    _assert_quota_pause(repo)

    handler._respond_unknown_instruction(_build_event("huh"), "")
    _assert_quota_pause(repo)

    repo.usage = 0
    line.sent_texts.clear()
    handler._handle_translation_flow(_build_event("new month"), sender_name="user", translation_enabled=False)

    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert repo.usage == 1
    reset = "\n".join(line.sent_texts)
    assert "has reset" in reset
    assert _next_month(datetime.now(timezone.utc)) in reset


class _FailingLine(RecordingLineClient):
    def reply_text(self, _reply_token, _text):
        raise RuntimeError("line down")

    def reply_messages(self, _reply_token, _messages):
        raise RuntimeError("line down")


def test_approaching_notice_build_failure_keeps_translation_and_releases_claim():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=39, paid=False)
    handler = _with_checkout(_build_handler(repo, line, translation))

    def boom(*_args, **_kwargs):
        raise RuntimeError("gemini down")

    handler._build_approaching_notice_text = boom  # type: ignore[method-assign]

    handler._handle_translation_flow(_build_event("near"), sender_name="user", translation_enabled=True)

    assert translation.calls == 1
    assert repo.usage == 40
    assert repo.approaching_claimed is False
    joined = "\n".join(line.sent_texts)
    assert "(en)" in joined
    assert "40 of 50" not in joined
    assert "has used" not in joined


def test_approaching_notice_send_failure_releases_claim():
    repo = ProQuotaRepo(initial_usage=39, paid=False)
    handler = _with_checkout(_build_handler(repo, _FailingLine(), RecordingTranslationService()))

    with pytest.raises(RuntimeError, match="line down"):
        handler._handle_translation_flow(_build_event("near"), sender_name="user", translation_enabled=True)

    assert repo.usage == 40
    assert repo.approaching_claimed is False


def test_quota_mention_reminder_send_failure_releases_claim():
    repo = ProQuotaRepo(
        initial_usage=50,
        paid=False,
        notice_plan="free",
        translation_enabled=False,
        paused_reason="quota",
    )
    handler = _with_checkout(_build_handler(repo, _FailingLine(), RecordingTranslationService()))
    runtime = repo.fetch_translation_runtime_state("group")

    with pytest.raises(RuntimeError, match="line down"):
        handler._reply_quota_pause_reminder(_build_event("@bot"), runtime, repeat="silent")

    assert repo.mention_reminders == set()
    assert repo.paused_reason == "quota"


def test_free_40_sends_approaching_notice_once():
    line = _CapturingLine()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=39, paid=False)
    handler = _with_checkout(_build_handler(repo, line, translation))

    handler._handle_translation_flow(_build_event("near"), sender_name="user", translation_enabled=True)

    assert repo.usage == 40
    assert translation.calls == 1
    joined = "\n".join(line.sent_texts)
    assert "40 of 50" in joined
    assert "Translation stopped" not in joined
    assert "https://short.example.com/cs" not in joined
    assert _uris(line.payloads) == ["https://short.example.com/cs"]

    line.sent_texts.clear()
    line.payloads.clear()
    handler._handle_translation_flow(_build_event("again"), sender_name="user", translation_enabled=True)

    assert repo.usage == 41
    assert "has used" not in "\n".join(line.sent_texts)
    assert _uris(line.payloads) == []


def test_free_49_translates_without_stop_notice():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=48, paid=False)
    repo.approaching_claimed = True
    handler = _with_checkout(_build_handler(repo, line, translation))

    handler._handle_translation_flow(_build_event("forty-nine"), sender_name="user", translation_enabled=True)

    assert repo.usage == 49
    assert translation.calls == 1
    joined = "\n".join(line.sent_texts)
    assert "Translation stopped" not in joined
    assert "has used" not in joined


def test_free_50_translates_and_sends_stop_notice():
    line = _CapturingLine()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=49, paid=False)
    handler = _with_checkout(_build_handler(repo, line, translation))

    handler._handle_translation_flow(_build_event("fiftieth"), sender_name="user", translation_enabled=True)

    assert repo.usage == 50
    assert translation.calls == 1
    assert repo.notice_plan == "free"
    joined = "\n".join(line.sent_texts)
    assert "Translation stopped" in joined
    assert "used all 50 free messages" in joined
    assert _next_month(datetime.now(timezone.utc)) in joined
    assert _uris(line.payloads) == ["https://short.example.com/cs"]


def test_free_50_persists_quota_pause_before_the_next_message():
    """50件目の停止案内と同時に quota 停止を残す。51件目を待たない。"""

    line = _CapturingLine()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=49, paid=False)
    handler = _with_checkout(_build_handler(repo, line, translation))

    handler._handle_translation_flow(_build_event("fiftieth"), sender_name="user", translation_enabled=True)

    assert repo.usage == 50
    assert translation.calls == 1
    assert repo.notice_plan == "free"
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"
    assert "Translation stopped" in "\n".join(line.sent_texts)

    line.sent_texts.clear()
    line.payloads.clear()
    handler._command_router = _ActionRouter("resume")
    handler._handle_command(_build_event("resume"), "resume")

    resumed = "\n".join(line.sent_texts)
    assert "still paused" in resumed
    assert "I will resume" not in resumed
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"

    line.sent_texts.clear()
    handler._command_router = _ActionRouter("pause")
    handler._handle_command(_build_event("pause"), "pause")

    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"
    assert "I will pause" not in "\n".join(line.sent_texts)

    line.sent_texts.clear()
    handler._handle_translation_flow(_build_event("fifty-first"), sender_name="user", translation_enabled=True)

    assert line.sent_texts == []
    assert translation.calls == 1
    assert repo.usage == 50
    assert repo.notice_plan == "free"
    assert repo.paused_reason == "quota"

    repo.usage = 0
    line.sent_texts.clear()
    handler._handle_translation_flow(_build_event("new month"), sender_name="user", translation_enabled=False)

    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert translation.calls == 2
    assert repo.usage == 1
    reset = "\n".join(line.sent_texts)
    assert "has reset" in reset
    assert _next_month(datetime.now(timezone.utc)) in reset


def test_free_51_pauses_without_repeating_the_notice():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=50, paid=False, notice_plan="free")
    handler = _with_checkout(_build_handler(repo, line, translation))

    handled = handler._handle_translation_flow(_build_event("blocked"), sender_name="user", translation_enabled=True)

    assert handled is True
    assert translation.calls == 0
    assert repo.translation_enabled is False
    assert repo.paused_reason == "quota"
    assert line.sent_texts == []


def test_quota_reset_resumes_translation_and_says_so():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(
        initial_usage=0,
        paid=False,
        translation_enabled=False,
        paused_reason="quota",
    )
    handler = _build_handler(repo, line, translation)

    handler._handle_translation_flow(_build_event("new month"), sender_name="user", translation_enabled=False)

    assert repo.translation_enabled is True
    assert repo.paused_reason is None
    assert translation.calls == 1
    assert repo.usage == 1
    joined = "\n".join(line.sent_texts)
    assert "has reset" in joined
    assert "Translation stopped" not in joined
    assert _next_month(datetime.now(timezone.utc)) in joined


def test_paused_mention_reminds_once_per_day_and_chat_stays_quiet():
    line = _CapturingLine()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(
        initial_usage=50,
        paid=False,
        notice_plan="free",
        translation_enabled=False,
        paused_reason="quota",
    )
    handler = _with_checkout(_build_handler(repo, line, translation))
    handler._command_router = _BoomRouter()

    mention = replace(
        _build_event("@bot"),
        mentionees=[models.Mentionee(index=0, length=4, is_self=True)],
        destination="Ubot",
    )
    handler.handle(mention)

    joined = "\n".join(line.sent_texts)
    assert "still paused" in joined
    assert "used all 50 free messages" in joined
    assert _uris(line.payloads) == ["https://short.example.com/cs"]
    assert repo.translation_enabled is False
    assert translation.calls == 0

    line.sent_texts.clear()
    line.payloads.clear()
    handler.handle(mention)
    assert line.sent_texts == []
    assert repo.translation_enabled is False

    handler._handle_translation_flow(_build_event("hello"), sender_name="user", translation_enabled=False)
    assert line.sent_texts == []
    assert translation.calls == 0


def test_paused_resume_does_not_claim_translation_is_back():
    line = RecordingLineClient()
    repo = ProQuotaRepo(
        initial_usage=50,
        paid=False,
        notice_plan="free",
        translation_enabled=False,
        paused_reason="quota",
    )
    handler = _with_checkout(_build_handler(repo, line, RecordingTranslationService()))
    handler._command_router = _ResumeRouter()
    event = _build_event("resume")

    handler._handle_command(event, "resume")
    assert repo.translation_enabled is False
    assert "still paused" in "\n".join(line.sent_texts)
    assert "I will resume" not in "\n".join(line.sent_texts)

    line.sent_texts.clear()
    handler._handle_command(event, "resume")
    assert repo.translation_enabled is False
    joined = "\n".join(line.sent_texts)
    assert "stays paused" in joined
    assert "earlier today" in joined
    assert "I will resume" not in joined


def test_paused_subscription_menu_is_not_replaced_by_the_quota_reminder():
    line = _CapturingLine()
    repo = ProQuotaRepo(
        initial_usage=50,
        paid=False,
        notice_plan="free",
        translation_enabled=False,
        paused_reason="quota",
    )
    handler = _with_checkout(_build_handler(repo, line, RecordingTranslationService()))
    handler._command_router = _MenuRouter()

    handler._handle_command(_build_event("plans"), "plans")

    assert repo.translation_enabled is False
    assert "still paused" not in "\n".join(line.sent_texts)
    assert any(message.get("type") == "template" for payload in line.payloads for message in payload)


def test_limit_notice_localizes_known_languages_and_keeps_english_for_unregistered():
    class _Repo:
        def fetch_group_languages(self, _group_id):
            return ["ja", "xx", "en"]

    class _Translator:
        def translate(self, text, languages):
            assert "xx" in languages
            return [models.TranslationResult(lang="ja", text="翻訳が止まりました")]

    handler = MessageHandler(
        line_client=_Repo(),
        translation_service=_Repo(),
        interface_translation=_Translator(),
        language_detector=_Repo(),
        language_pref_service=_Repo(),
        command_router=_Repo(),
        repo=_Repo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        stripe_secret_key="sk",
        stripe_price_monthly_id="price",
        subscription_frontend_base_url="https://frontend.example.com",
        checkout_api_base_url="https://api.example.com",
    )
    handler._subscription_service.create_checkout_url = lambda _gid: "https://short.example.com/cs"  # type: ignore[attr-defined]

    notice, url = handler._build_limit_reached_notice_text(
        "group1",
        plan_key="free",
        limit=50,
        period_key="2026-10-01",
        period_end=datetime(2024, 3, 17, tzinfo=timezone.utc),
    )

    assert url == "https://short.example.com/cs"
    assert "https://short.example.com/cs" not in notice
    assert notice.count("翻訳が止まりました") == 1
    # en と未登録の xx は英語原文のまま
    assert notice.count("Translation stopped") == 2
    assert "2026-11-01" in notice


def test_limit_notice_includes_every_configured_language():
    class _Repo:
        def fetch_group_languages(self, _group_id):
            return ["en", "ja", "th"]

    class _Translator:
        def translate(self, text, languages):
            return [
                models.TranslationResult(lang=lang, text=f"[{lang}]{text}")
                for lang in languages
                if lang in {"ja", "th"}
            ]

    handler = MessageHandler(
        line_client=_Repo(),
        translation_service=_Repo(),
        interface_translation=_Translator(),
        language_detector=_Repo(),
        language_pref_service=_Repo(),
        command_router=_Repo(),
        repo=_Repo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )
    handler._subscription_service.create_checkout_url = lambda _gid: "https://short.example.com/cs"  # type: ignore[attr-defined]

    notice, url = handler._build_limit_reached_notice_text(
        "group1",
        plan_key="free",
        limit=50,
        period_key="2026-10-01",
    )

    assert url == "https://short.example.com/cs"
    assert "Translation stopped" in notice
    assert "[ja]" in notice
    assert "[th]" in notice
    assert "2026-11-01" in notice
    assert "¥" not in notice
