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
    # ラテン文字が過半数なら英語。かな・漢字が過半数なら日本語
    assert detector.resolve_instruction_language("Please confirm 明日の予定", group_languages=GROUP) == "en"
    assert detector.resolve_instruction_language("Add English と 日本語", group_languages=GROUP) == "en"
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
        ("Please confirm 明日の予定", "en"),
        # 「Add English と 日本語」は言語の追加として扱う。未知の混在文は別の文で見る
        ("Add the notes と 予定", "en"),
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

    assert translator.calls == []
    assert "日本語" in handler._line.last_text
    assert "英語" in handler._line.last_text
    assert "Change language settings" not in (handler._line.last_text or "")
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

    assert translator.calls == []
    assert "日本語" in handler._line.last_text
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
    assert translator.calls == []
    assert "日本語" in handler._line.last_text
    assert "英語" in handler._line.last_text
    assert "Korean" not in handler._line.last_text
    assert "韓国語" not in handler._line.last_text


def test_shared_script_prefers_group_language_then_restricted_detection():
    """アラビア文字などを、グループの言語 → その集合の langdetect → 既定、の順で決める。"""

    detector = LanguageDetectionService()
    persian = "سلام، حالت چطوره؟ لطفاً زبان را اضافه کنید"
    urdu = "میں اردو بولتا ہوں، براہ کرم اردو شامل کریں"
    ukrainian = "Привіт, як справи? Будь ласка, додайте українську мову до перекладу."
    russian = "Привет, как дела? Пожалуйста, добавьте русский язык к переводу."
    spanish = "Hola, ¿cómo estás? Por favor dime los idiomas de traducción actuales."
    english = "how do I use this bot?"

    # ペルシャ語を ar と誤判定しても、グループに fa があれば fa
    assert detector.resolve_instruction_language(persian, group_languages=["fa", "en"], detected="ar") == "fa"
    assert detector.resolve_instruction_language(urdu, group_languages=["ur", "en"], detected="ar") == "ur"
    # グループに両言語があるときは、その集合に限って区別する
    assert detector.resolve_instruction_language(ukrainian, group_languages=["uk", "ru"]) == "uk"
    assert detector.resolve_instruction_language(russian, group_languages=["uk", "ru"]) == "ru"
    assert detector.resolve_instruction_language(ukrainian, group_languages=["uk", "en"]) == "uk"
    assert detector.resolve_instruction_language(russian, group_languages=["ru", "en"]) == "ru"
    assert detector.resolve_instruction_language(spanish, group_languages=["es", "en"]) == "es"
    assert detector.resolve_instruction_language(english, group_languages=["es", "en"]) == "en"
    # グループにその文字体系が無いときは、集合に限った langdetect を使う（既定の ar にしない）
    assert detector.resolve_instruction_language(
        "سلام، حال شما چطور است؟ لطفا زبان ترجمه را به من بگویید.",
        group_languages=["ja", "en"],
    ) == "fa"
    # 短いラテン文は誤検知よりグループ言語を優先する
    assert detector.resolve_instruction_language("Hola", group_languages=["es", "ja"]) == "es"
    hindi = "नमस्ते, आप कैसे हैं? कृपया वर्तमान अनुवाद भाषाएँ बताएं।"
    marathi = "नमस्कार, तुम्ही कसे आहात? कृपया सध्याच्या भाषांतर भाषा सांगा."
    assert detector.resolve_instruction_language(hindi, group_languages=["hi", "mr"]) == "hi"
    assert detector.resolve_instruction_language(marathi, group_languages=["hi", "mr"]) == "mr"
    # かなとハングルは従来どおり一意
    assert detector.resolve_instruction_language("こんにちは", group_languages=["fa", "ar"]) == "ja"
    assert detector.resolve_instruction_language("안녕하세요", group_languages=["fa", "ar"]) == "ko"
    # ベンガル文字・タミル文字・ヘブライ文字はグループ先頭の ja に落とさない
    bengali = "আপনি কেমন আছেন? অনুগ্রহ করে বাংলা যোগ করুন।"
    tamil = "வணக்கம், தமிழ் மொழியை சேர்க்கவும்."
    hebrew = "שלום, איך מאפסים את כל השפות?"
    assert detector.resolve_instruction_language(bengali, group_languages=["ja", "en", "bn"]) == "bn"
    assert detector.resolve_instruction_language(tamil, group_languages=["ja", "en"]) == "ta"
    assert detector.resolve_instruction_language(hebrew, group_languages=["ja", "en"]) == "he"
    # 未分類の文字体系は group[0] ではなく生の langdetect を使う
    assert detector.resolve_instruction_language("ᎣᏏᏲ", group_languages=["ja", "en"], detected="chr") == "chr"


class _ResetRouter:
    def __init__(self, operation, explicit_reset=False):
        self.operation = operation
        self.explicit_reset = explicit_reset

    def decide(self, _text):
        return models.CommandDecision(
            action="language_settings",
            operation=self.operation,
            instruction_language="ja",
            ack_text="リセットしました",
            explicit_reset=self.explicit_reset,
        )


def test_unnamed_language_request_does_not_reset_settings():
    """『言語を追加したい』は言語名もリセット要求も無いので、設定を消さず一覧を返す。"""

    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    handler._command_router = _ResetRouter("reset_all")
    text = "言語を追加したい"

    handler._handle_command(_event(f"@bot {text}"), text)

    assert repo.reset_called is False
    assert repo.enabled == []
    assert translator.calls == []
    assert "日本語" in handler._line.last_text
    assert "英語" in handler._line.last_text


def test_spanish_and_persian_reset_requests_clear_languages():
    """英語・日本語以外でも、モデルの explicit_reset があれば設定を消す。"""

    for text in ("borra todos los idiomas", "همه زبان‌ها را پاک کن"):
        repo = _Repo(GROUP)
        translator = _Translator()
        handler = _handler(repo, translator)
        handler._command_router = _ResetRouter("reset_all", explicit_reset=True)

        handler._handle_command(_event(f"@bot {text}"), text)

        assert repo.reset_called is True, text
        assert False in repo.enabled


def test_current_languages_question_does_not_reset_settings():
    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    handler._command_router = _ResetRouter("reset_all", explicit_reset=False)
    text = "今の翻訳言語は何?"

    handler._handle_command(_event(f"@bot {text}"), text)

    assert repo.reset_called is False
    assert repo.enabled == []


def test_explicit_reset_request_still_clears_languages():
    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    handler._command_router = _ResetRouter("reset_all")
    text = "言語設定をリセットして"

    handler._handle_command(_event(f"@bot {text}"), text)

    assert repo.reset_called is True
    assert False in repo.enabled


def test_language_change_is_not_treated_as_current_languages_question():
    assert not is_current_languages_question("言語を英語にして")
    assert not is_current_languages_question("翻訳言語を追加して")
    assert not is_current_languages_question("change the language to French")
    assert not is_current_languages_question("how do I use this bot?")
    assert is_current_languages_question("今の翻訳言語は何?")
    assert is_current_languages_question("what are the current translation languages?")


def test_dominant_script_decides_mixed_reply_language():
    """英語の文に日本語が少しあるときは en。日本語が過半数なら ja。"""

    detector = LanguageDetectionService()
    assert detector.resolve_instruction_language(
        "What does ありがとう mean in English?",
        group_languages=GROUP,
    ) == "en"
    assert detector.resolve_instruction_language(
        "How do I say 日本語?",
        group_languages=GROUP,
    ) == "en"
    assert detector.resolve_instruction_language(
        "Please show the current languages, including 日本語",
        group_languages=GROUP,
    ) == "en"
    assert detector.resolve_instruction_language(
        "明日の会議は10時です please",
        group_languages=GROUP,
    ) == "ja"
    assert detector.resolve_instruction_language(
        "Hello こんにちは、元気？",
        group_languages=GROUP,
    ) == "ja"
    # URL とメンション、絵文字のラテン文字や記号は数えない
    assert detector.resolve_instruction_language(
        "@Christopher https://example.com/Current_translation_languages 今の翻訳言語は何?",
        preferred="ko",
        group_languages=GROUP,
    ) == "ja"
    assert detector.resolve_instruction_language(
        "What does ありがとう mean in English? 👍😂🎉",
        group_languages=GROUP,
    ) == "en"


def test_status_set_is_current_languages_question():
    """状態の set は現在の言語の質問。命令の set は変更のまま。"""

    assert is_current_languages_question("Which languages are currently set?")
    assert is_current_languages_question("What languages are set now?")
    assert is_current_languages_question("Which language is set?")
    assert is_current_languages_question("What languages are set?")
    assert not is_current_languages_question("set English and Thai")
    assert not is_current_languages_question("please set English and Thai")
    assert not is_current_languages_question("Please set the current languages to English and Thai")
    assert not is_current_languages_question("Which languages are currently set? Please set English")


def test_support_questions_are_not_current_languages_questions():
    """対応言語の質問は、現在の設定を聞く文ではない。"""

    assert not is_current_languages_question("Which languages does this bot support?")
    assert not is_current_languages_question("What languages do you support?")
    assert not is_current_languages_question("Which languages does this bot currently support?")
    assert not is_current_languages_question("どの言語に対応していますか")
    assert not is_current_languages_question("対応している言語は何ですか")
    assert not is_current_languages_question("どの言語")
    assert not is_current_languages_question("show the languages")
    assert is_current_languages_question("今の翻訳言語は何?")
    assert is_current_languages_question("現在の言語を教えて")
    assert is_current_languages_question("設定している言語は何?")
    assert is_current_languages_question("どの言語が設定されていますか")
    assert is_current_languages_question("what are the current translation languages?")
    assert is_current_languages_question("Which languages are currently set?")


def test_distinct_language_sets_do_not_grow_the_factory_cache():
    """言語集合がいくつあっても、単語確率表の複製キャッシュは上限を超えない。"""

    import langdetect.detector_factory as detector_factory

    from src.domain.services import language_detection_service as svc

    detector_factory.init_factory()
    shared_map = detector_factory._factory.word_lang_prob_map
    names = sorted(svc._profile_names())
    svc._RESTRICTED_FACTORIES.clear()
    sample = "Please tell me which translation languages are currently set for this group."
    seen = 0
    for index, left in enumerate(names):
        right = names[(index + 7) % len(names)]
        if left == right:
            continue
        svc._restricted_detect(sample, [left, right])
        seen += 1
    assert seen > svc._FACTORY_CACHE_LIMIT
    assert len(svc._RESTRICTED_FACTORIES) <= svc._FACTORY_CACHE_LIMIT
    assert detector_factory._factory.word_lang_prob_map is shared_map


def test_status_set_question_lists_group_languages():
    repo = _Repo(GROUP)
    translator = _Translator()
    handler = _handler(repo, translator)
    text = "Which languages are currently set?"

    handler._handle_command(_event(text), text)

    assert translator.calls == []
    assert handler._line.last_text == "The current translation languages are Japanese, English."
    assert repo.reset_called is False
