"""unknown 経路の返信言語と、現在の翻訳言語を尋ねる質問。"""

from src.app.handlers.message_handler import UNKNOWN_INSTRUCTION_BASE, MessageHandler
from src.domain import models
from src.domain.services.current_languages_question import is_current_languages_question
from src.domain.services.language_detection_service import LanguageDetectionService


GROUP = ["ja", "en"]
GARBLED_NAME = "@STG \uFFFD\u0296\uFFFDAI"


class _Line:
    def __init__(self):
        self.last_text = None

    def reply_text(self, _token, text):
        self.last_text = text

    def reply_messages(self, *_args, **_kwargs):
        return None


class _Translator:
    def __init__(self):
        self.calls = []

    def translate(self, **kwargs):
        self.calls.append(kwargs)
        lang = kwargs["candidate_languages"][0]
        return [models.TranslationResult(lang=lang, text=f"[{lang}]{kwargs['message_text']}")]


class _Repo:
    def __init__(self, languages):
        self.languages = list(languages)
        self.reset_called = False
        self.enabled = []

    def fetch_group_languages(self, _group_id):
        return list(self.languages)

    def fetch_translation_runtime_state(self, _group_id):
        return models.TranslationRuntimeState(
            translation_enabled=True,
            group_languages=list(self.languages),
            subscription_status=None,
            period_start=None,
            period_end=None,
            period_key="2026-10",
            usage=0,
            limit_notice_plan=None,
        )

    def set_translation_enabled(self, _group_id, enabled):
        self.enabled.append(enabled)

    def reset_group_language_settings(self, *_args):
        self.reset_called = True

    def is_translation_enabled(self, *_args):
        return True

    def get_usage(self, *_args, **_kwargs):
        return 0

    def get_subscription_period(self, *_args):
        return (None, None, None)


class _UnknownRouter:
    def decide(self, _text):
        # STG では language_settings の低確信度がここに落ちる
        return models.CommandDecision(action="unknown", instruction_language="", ack_text="")


class _ShowRouter:
    def decide(self, _text):
        return models.CommandDecision(
            action="language_settings",
            operation="show",
            instruction_language="ko",
            ack_text="",
        )


def _handler(repo, translator):
    return MessageHandler(
        line_client=_Line(),
        translation_service=translator,
        interface_translation=translator,
        language_detector=LanguageDetectionService(),
        language_pref_service=object(),
        command_router=_UnknownRouter(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="通訳AI",
    )


def _event(text, mentionees=None):
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text=text,
        user_id="U",
        group_id="G",
        sender_type="group",
        destination="BOT",
        mentionees=mentionees or [],
    )


def _langs(translator):
    return [call["candidate_languages"] for call in translator.calls]


def test_kanji_heavy_japanese_is_not_resolved_as_korean():
    """langdetect は『今の翻訳言語は何?』を ko と誤ることがある。返信言語には使わない。"""

    detector = LanguageDetectionService()
    text = "今の翻訳言語は何?"
    assert detector.resolve_instruction_language(text, preferred="ko", group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language(text, group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language(
        f"\uFFFD\u0296\uFFFDAI {text}",
        group_languages=GROUP,
    ) == "ja"


def test_resolve_follows_message_language_or_group_language():
    detector = LanguageDetectionService()

    assert detector.resolve_instruction_language("how do I use this bot?", group_languages=GROUP) == "en"
    assert detector.resolve_instruction_language("開始時間は何?", group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language("言語設定", group_languages=GROUP) == "ja"
    # かなを含む混合文は日本語。ラテン文字が多いと langdetect は en を返す
    assert detector.resolve_instruction_language("Please confirm 明日の予定", group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language("Add English と 日本語", group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language("Hello こんにちは、元気？", group_languages=GROUP) == "ja"
    # 本文がハングルなら、グループに無くても韓国語
    assert detector.resolve_instruction_language("지금 뭐해?", group_languages=GROUP) == "ko"
    # 文字が無いときはグループ言語。無関係な言語にはしない
    assert detector.resolve_instruction_language("👍😂🎉", group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language("12345 678", group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language("👍😂🎉", group_languages=["en", "ja"]) == "en"
    assert detector.resolve_instruction_language("12345 678", group_languages=["en", "ja"]) == "en"
    assert detector.resolve_instruction_language("\uFFFD\u0296\uFFFD", group_languages=GROUP) == "ja"
    assert detector.resolve_instruction_language("Bonjour, comment allez-vous aujourd'hui?", group_languages=GROUP) == "fr"
    assert detector.resolve_instruction_language("言語設定", group_languages=["zh-tw", "en"]) == "zh-tw"


def test_unknown_mentions_reply_in_the_message_language():
    cases = [
        ("開始時間は何?", "ja"),
        ("言語設定", "ja"),
        ("how do I use this bot?", "en"),
        ("Please confirm 明日の予定", "ja"),
        ("Add English と 日本語", "ja"),
        ("👍😂🎉", "ja"),
        ("12345 678", "ja"),
        (f"\uFFFD\u0296\uFFFDAI 開始時間は何?", "ja"),
        ("지금 뭐해?", "ko"),
    ]
    for text, expected in cases:
        assert not is_current_languages_question(text)
        repo = _Repo(GROUP)
        translator = _Translator()
        handler = _handler(repo, translator)
        handler._handle_command(_event(f"@bot {text}"), text)
        assert _langs(translator) == [[expected]], text
        assert "ko" not in (handler._line.last_text or "") or expected == "ko"
        assert UNKNOWN_INSTRUCTION_BASE.split("\n", 1)[0] in translator.calls[0]["message_text"]


def test_garbled_mention_name_asks_current_languages_in_japanese():
    question = "今の翻訳言語は何?"
    text = f"{GARBLED_NAME} {question}"
    mention = models.Mentionee(index=0, length=len(GARBLED_NAME), mention_type="user", user_id="BOT", is_self=True)
    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    event = _event(text, mentionees=[mention])

    command = handler._extract_command_text(event)

    assert command == question
    handler._handle_command(event, command)

    assert translator.calls
    assert translator.calls[0]["candidate_languages"] == ["ja"]
    assert "Japanese" in translator.calls[0]["message_text"]
    assert "English" in translator.calls[0]["message_text"]
    assert "Change language settings" not in translator.calls[0]["message_text"]
    assert repo.reset_called is False
    assert repo.enabled == []


def test_short_mention_span_keeps_garbled_name_and_still_replies_in_japanese():
    question = "今の翻訳言語は何?"
    text = f"{GARBLED_NAME} {question}"
    mention = models.Mentionee(index=0, length=len("@STG"), mention_type="user", user_id="BOT", is_self=True)
    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    event = _event(text, mentionees=[mention])
    command = handler._extract_command_text(event)

    assert "\uFFFD" in command
    assert "\u0296" in command
    handler._handle_command(event, command)

    assert translator.calls[0]["candidate_languages"] == ["ja"]
    assert "Japanese" in translator.calls[0]["message_text"]
    assert repo.reset_called is False


def test_english_current_languages_question_lists_group_languages():
    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    text = "what are the current translation languages?"

    handler._handle_command(_event(text), text)

    # 英語はベース原文をそのまま返す
    assert translator.calls == []
    assert handler._line.last_text == "The current translation languages are Japanese, English."
    assert repo.reset_called is False


def test_language_settings_show_does_not_reset_and_ignores_unrelated_language():
    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    handler._command_router = _ShowRouter()

    handler._handle_command(_event("@bot 今の翻訳言語は何?"), "今の翻訳言語は何?")

    assert repo.reset_called is False
    assert translator.calls[0]["candidate_languages"] == ["ja"]
    assert "Korean" not in translator.calls[0]["message_text"]


def test_language_change_is_not_treated_as_current_languages_question():
    assert not is_current_languages_question("言語を英語にして")
    assert not is_current_languages_question("翻訳言語を追加して")
    assert not is_current_languages_question("change the language to French")
    assert not is_current_languages_question("how do I use this bot?")
    assert is_current_languages_question("今の翻訳言語は何?")
    assert is_current_languages_question("what are the current translation languages?")
