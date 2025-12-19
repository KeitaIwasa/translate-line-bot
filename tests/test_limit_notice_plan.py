from datetime import datetime, timezone

from src.app.handlers.message_handler import MessageHandler
from src.domain import models


class _DummyInterfaceTranslation:
    """インターフェース翻訳を呼ばせないためのダミー。"""


class _DummyTranslation:
    """翻訳を呼ばせないためのダミー。"""


class _DummyLanguageDetector:
    """言語検知を呼ばせないためのダミー。"""


class _DummyLanguagePref:
    """言語解析を呼ばせないためのダミー。"""


class _DummyCommandRouter:
    """コマンド判定を呼ばせないためのダミー。"""


class _DummyLine:
    """LINE送信のダミー。"""

    def __init__(self) -> None:
        self.replies = []

    def reply_messages(self, reply_token, messages):
        self.replies.append((reply_token, messages))

    def reply_text(self, reply_token, text):
        self.replies.append((reply_token, [{"type": "text", "text": text}]))


class _FakeRepo:
    """limit_notice_plan の更新有無だけ検証するための最小Repo。"""

    def __init__(self, usage: int) -> None:
        self._usage = usage
        self.set_calls = []

    def get_subscription_period(self, _group_id):
        return (None, None, None)

    def get_usage(self, _group_id, _period_key):
        return self._usage

    def get_limit_notice_plan(self, _group_id, _period_key):
        return None

    def set_limit_notice_plan(self, group_id, period_key, plan):
        self.set_calls.append((group_id, period_key, plan))

    def fetch_group_languages(self, _group_id):
        # 翻訳を発生させないため英語だけにする
        return ["en"]


def test_pause_notice_over_quota_sets_limit_notice_plan():
    line = _DummyLine()
    repo = _FakeRepo(usage=50)
    handler = MessageHandler(
        line_client=line,
        translation_service=_DummyTranslation(),
        interface_translation=_DummyInterfaceTranslation(),
        language_detector=_DummyLanguageDetector(),
        language_pref_service=_DummyLanguagePref(),
        command_router=_DummyCommandRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        free_quota_per_month=50,
    )

    event = models.MessageEvent(
        event_type="message",
        reply_token="r1",
        group_id="group1",
        user_id="user1",
        sender_type="group",
        timestamp=0,
        text="hello",
    )

    handler._send_pause_notice(event)

    now = datetime.now(timezone.utc)
    expected_period_key = f"{now.year:04d}-{now.month:02d}-01"
    assert repo.set_calls == [("group1", expected_period_key, "free")]

