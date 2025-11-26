import os
import sys
import types

from language_preferences.service import LanguageOption, LanguagePreferenceResult, TextSet
from line_webhook import LineEvent

# --- bootstrap test environment before importing lambda_handler ---
os.environ.setdefault("LINE_CHANNEL_SECRET", "dummy")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "dummy")
os.environ.setdefault("GEMINI_API_KEY", "dummy")
os.environ.setdefault("NEON_DATABASE_URL", "postgres://dummy")

fake_sql_mod = types.ModuleType("psycopg.sql")
fake_sql_mod.SQL = lambda query: query

fake_psycopg_mod = types.ModuleType("psycopg")
fake_psycopg_mod.sql = fake_sql_mod
sys.modules.setdefault("psycopg", fake_psycopg_mod)
sys.modules.setdefault("psycopg.sql", fake_sql_mod)

fake_pool_mod = types.ModuleType("psycopg_pool")


class _DummyPool:
    def __init__(self, *_, **__):
        pass

    def connection(self):
        raise RuntimeError("Dummy pool has no connections")


fake_pool_mod.ConnectionPool = _DummyPool
sys.modules.setdefault("psycopg_pool", fake_pool_mod)

fake_langdetect_mod = types.ModuleType("langdetect")


class _DummyLangDetectException(Exception):
    pass


def _dummy_detect(_text: str) -> str:
    return "en"


fake_langdetect_mod.detect = _dummy_detect
fake_langdetect_mod.LangDetectException = _DummyLangDetectException
sys.modules.setdefault("langdetect", fake_langdetect_mod)

from src import lambda_handler


def test_language_enrollment_ignores_unsupported_in_confirm(monkeypatch):
    sent = {}

    class FakeLineClient:
        def reply_messages(self, reply_token, messages):
            sent["reply_token"] = reply_token
            sent["messages"] = messages

        def reply_text(self, reply_token, text):
            sent["reply_token"] = reply_token
            sent["messages"] = [{"type": "text", "text": text}]

        def get_display_name(self, *_args, **_kwargs):
            return None

    fake_supported = [
        LanguageOption(
            code="ja",
            primary_name="日本語",
            english_name="Japanese",
            thai_name="ญี่ปุ่น",
            supported=True,
        ),
        LanguageOption(
            code="ar",
            primary_name="アラビア語",
            english_name="Arabic",
            thai_name="อาหรับ",
            supported=True,
        ),
    ]
    fake_unsupported = [
        LanguageOption(
            code="sa",
            primary_name="संस्कृतम्",
            english_name="Sanskrit",
            thai_name="สันสกฤต",
            supported=False,
        ),
    ]

    fake_result = LanguagePreferenceResult(
        primary_language="ja",
        languages=fake_supported + fake_unsupported,
        confirm_text=TextSet(
            primary="日本語、アラビア語、サンスクリット語の翻訳を有効にしますか？",
            english="Japanese, Arabic, Sanskrit?",
            thai="ญี่ปุ่น อาหรับ สันสกฤต ใช่ไหม",
        ),
        completed_text=TextSet(primary="", english="", thai=""),
        cancel_text=TextSet(primary="", english="", thai=""),
        confirm_label="OK",
        cancel_label="Cancel",
    )

    monkeypatch.setattr(lambda_handler, "line_client", FakeLineClient())
    monkeypatch.setattr(
        lambda_handler,
        "language_pref_service",
        type("FakeLangService", (), {"analyze": lambda _self, _text: fake_result})(),
    )
    monkeypatch.setattr(lambda_handler.repositories, "record_language_prompt", lambda *args, **kwargs: None)

    event = LineEvent(
        event_type="message",
        reply_token="reply-token",
        timestamp=0,
        text="日本語、アラビア語、絵文字、サンスクリット語",
        user_id="U",
        group_id="G",
        sender_type="group",
        joined_user_ids=[],
        postback_data=None,
    )

    lambda_handler._attempt_language_enrollment(event)

    messages = sent["messages"]
    # 1. 未対応メッセージが先に送られる
    assert messages[0]["type"] == "text"
    assert "Sanskrit" in messages[0]["text"]

    # 2. 確認テンプレートには対応言語のみが含まれる
    template = messages[1]["template"]
    assert template["type"] == "confirm"
    assert template["text"] == "日本語、アラビア語の翻訳を有効にしますか？"

    confirm_payload = lambda_handler._decode_postback_payload(template["actions"][0]["data"])
    langs = [item["code"] for item in confirm_payload["languages"]]
    assert langs == ["ja", "ar"]
