from datetime import datetime, timezone

from src.app.handlers.message_handler import MessageHandler, USAGE_MESSAGE
from src.domain import models
from src.domain.models import TranslationResult


class _Repo:
    def fetch_group_languages(self, _group_id):
        return ["en", "ar"]


class _Handler(MessageHandler):
    def __init__(self):
        # バイパスして最低限の属性だけをセット
        pass

    def _invoke_translation_with_retry(self, *, candidate_languages, **_kwargs):
        # USAGE_MESSAGE をそのまま返すだけのダミー翻訳
        return [TranslationResult(lang=lang, text=f"{USAGE_MESSAGE} (AR)") for lang in candidate_languages]


def test_usage_response_wraps_bidi_lines():
    handler = _Handler()
    handler._repo = _Repo()
    handler._max_group_languages = 5

    # メソッド内で使う helper を流用するためクラスの関数をバインド
    handler._limit_language_codes = MessageHandler._limit_language_codes.__get__(handler, MessageHandler)
    handler._dedup_language_codes = MessageHandler._dedup_language_codes.__get__(handler, MessageHandler)

    result = handler._build_usage_response(instruction_lang="ar", group_id="G")
    lines = result.split("\n\n")

    # 英語行は LRM + LRE/PDF + LRM
    assert lines[0].startswith("\u200E\u202A") and lines[0].endswith("\u200E")
    # アラビア語行は RLM + RLE/PDF + RLM
    assert lines[1].startswith("\u200F\u202B") and lines[1].endswith("\u202C\u200F")
