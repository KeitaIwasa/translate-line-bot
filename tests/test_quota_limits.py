from datetime import datetime, timezone

from src.app.handlers.message_handler import MessageHandler
from src.domain import models


class RecordingLineClient:
    def __init__(self):
        self.sent_texts = []

    def reply_text(self, _reply_token, text):
        self.sent_texts.append(text)

    def reply_messages(self, _reply_token, messages):
        texts = [msg.get("text", "") for msg in messages if isinstance(msg, dict)]
        self.sent_texts.append("\n".join(texts))

    def get_display_name(self, *_args, **_kwargs):
        return None


class RecordingTranslationService:
    def __init__(self):
        self.calls = 0

    def translate(self, *, sender_name, message_text, timestamp, context_messages, candidate_languages):
        self.calls += 1
        return [
            models.TranslationResult(lang=lang, text=f"{message_text} ({lang})")
            for lang in candidate_languages
        ]


class NullInterfaceTranslation:
    def __init__(self):
        self.calls = 0

    def translate(self, *_args, **_kwargs):
        self.calls += 1
        return []


class NullLangPrefService:
    def analyze(self, _text: str):
        return None


class NullCommandRouter:
    def decide(self, _text: str):
        return models.CommandDecision(action="unknown", instruction_language="", ack_text="")


class ProQuotaRepo:
    def __init__(self, initial_usage: int, paid: bool = True, notice_plan: str | None = None):
        self.usage = initial_usage
        self.paid = paid
        self.translation_enabled = True
        self.notice_plan = notice_plan

    # group/lang settings
    def fetch_group_languages(self, _group_id):
        return ["en"]

    def fetch_recent_messages(self, _group_id, _limit):
        return []

    # usage / subscription
    def get_usage(self, _group_id, _period_key):
        return self.usage

    def get_limit_notice_plan(self, _group_id, _period_key):
        return self.notice_plan

    def increment_usage(self, _group_id, _period_key, increment: int = 1):
        self.usage += increment
        return self.usage

    def set_limit_notice_plan(self, _group_id, _period_key, plan: str):
        self.notice_plan = plan

    def get_subscription_period(self, _group_id):
        if self.paid:
            return ("active", datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 2, 1, tzinfo=timezone.utc))
        return (None, None, None)

    def set_translation_enabled(self, _group_id, enabled: bool):
        self.translation_enabled = enabled

    def is_translation_enabled(self, _group_id):
        return self.translation_enabled

    # unused interface stubs
    def ensure_group_member(self, *_args, **_kwargs):
        return None

    def insert_message(self, *_args, **_kwargs):
        return None

    def record_language_prompt(self, *_args, **_kwargs):
        return None

    def try_complete_group_languages(self, *_args, **_kwargs):
        return False

    def try_cancel_language_prompt(self, *_args, **_kwargs):
        return False

    def reset_group_language_settings(self, *_args, **_kwargs):
        return None

    def add_group_languages(self, *_args, **_kwargs):
        return None

    def remove_group_languages(self, *_args, **_kwargs):
        return None

    def record_bot_joined_at(self, *_args, **_kwargs):
        return None

    def fetch_bot_joined_at(self, *_args, **_kwargs):
        return None

    def upsert_subscription(self, *_args, **_kwargs):
        return None

    def update_subscription_status(self, *_args, **_kwargs):
        return None


def _build_event(text: str = "hello"):
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
        text=text,
        user_id="user",
        group_id="group",
        sender_type="group",
    )


def _build_handler(repo: ProQuotaRepo, line: RecordingLineClient, translation: RecordingTranslationService):
    return MessageHandler(
        line_client=line,
        translation_service=translation,
        interface_translation=NullInterfaceTranslation(),
        language_detector=None,  # not used in these tests
        language_pref_service=NullLangPrefService(),
        command_router=NullCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        free_quota_per_month=50,
        pro_quota_per_month=8000,
    )


def test_pro_plan_blocks_when_usage_already_over_limit():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=8000, paid=True)
    handler = _build_handler(repo, line, translation)

    handler._handle_translation_flow(_build_event(), sender_name="user", translation_enabled=True)

    assert translation.calls == 0
    assert any("Pro plan monthly limit" in text for text in line.sent_texts)
    assert repo.usage == 8000
    assert repo.translation_enabled is True


def test_pro_plan_warns_on_last_allowed_message_and_translates():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=7999, paid=True)
    handler = _build_handler(repo, line, translation)

    handler._handle_translation_flow(_build_event("hi"), sender_name="user", translation_enabled=True)

    assert repo.usage == 8000
    # 8000通目は翻訳して通知
    assert translation.calls == 1
    assert any("Pro plan" in text for text in line.sent_texts)
    assert repo.notice_plan == "pro"


def test_pro_plan_over_limit_does_not_renotify_when_flag_set():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=8001, paid=True, notice_plan="pro")
    handler = _build_handler(repo, line, translation)

    handled = handler._handle_translation_flow(_build_event("over"), sender_name="user", translation_enabled=True)

    assert handled is True
    assert translation.calls == 0
    assert line.sent_texts == []  # 通知は再送しない


def test_free_notice_not_repeated_after_upgrade_then_pro_notice_sent():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    # Freeで既に通知済みの月にProへアップグレード
    repo = ProQuotaRepo(initial_usage=50, paid=False, notice_plan="free")
    handler = _build_handler(repo, line, translation)

    # Proとして上限到達はまだ先なので、この時点では通知されないが、フラグは維持
    handler._handle_translation_flow(_build_event("hi"), sender_name="user", translation_enabled=True)
    assert repo.notice_plan == "free"

    # Pro課金状態に切り替え、usageを上限近くまで進めて通知
    repo.paid = True
    repo.usage = 7999
    line.sent_texts.clear()
    handler._handle_translation_flow(_build_event("again"), sender_name="user", translation_enabled=True)

    assert repo.notice_plan == "pro"
    assert any("Pro plan" in text for text in line.sent_texts)


def test_free_notice_blocks_processing_when_unpaid_and_non_command():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=60, paid=False, notice_plan="free")
    handler = _build_handler(repo, line, translation)

    # translation_enabled True でも、free通知済みなら処理スキップ
    handled = handler._handle_translation_flow(_build_event("hello"), sender_name="user", translation_enabled=True)

    assert handled is True
    assert translation.calls == 0
    assert repo.usage == 60  # カウント増えない
    assert line.sent_texts == []


def test_free_plan_sends_notice_after_50th_translation():
    line = RecordingLineClient()
    translation = RecordingTranslationService()
    repo = ProQuotaRepo(initial_usage=49, paid=False, notice_plan=None)
    handler = _build_handler(repo, line, translation)

    handled = handler._handle_translation_flow(_build_event("msg"), sender_name="user", translation_enabled=True)

    assert handled is True
    assert repo.usage == 50
    assert translation.calls == 1  # 50通目は翻訳実行
    assert repo.notice_plan == "free"  # 通知フラグ更新
    assert any("Free quota" in text for text in line.sent_texts)
