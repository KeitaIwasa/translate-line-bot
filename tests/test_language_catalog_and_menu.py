"""カタログ外は propose へ渡し、Quick Reply と漢字だけの日本語を言語ごとに返す。"""

from src.app.handlers.postback_handler import PostbackHandler
from src.domain import models
from src.domain.services.language_mention import read_mention, reply_family
from src.domain.services.language_settings_service import normalize_catalog_selection
from tests.test_language_setup_flow import _Line, _Repo, _Subs, _event, _handler


class _Translated:
    def __init__(self, lang, text):
        self.lang = lang
        self.text = text


class _Interface:
    def __init__(self):
        self.calls = []

    def translate(self, base, targets):
        self.calls.append((base, list(targets)))
        return [_Translated(code, f"{code}: {base.splitlines()[0]}") for code in targets]


class _Pref:
    def __init__(self, preference=None):
        self.calls = []
        self.preference = preference

    def analyze(self, text):
        self.calls.append(text)
        return self.preference


class _PromptRepo(_Repo):
    def __init__(self, languages=None):
        super().__init__(languages)
        self.prompts = []

    def record_language_prompt(self, group_id):
        self.prompts.append(group_id)
        return "stamp-1"


def _preference(*pairs, primary="ja"):
    return models.LanguagePreference(
        supported=[models.LanguageChoice(code=code, name=name) for code, name in pairs],
        unsupported=[],
        primary_language=primary,
        confirm_label="OK",
        cancel_label="Cancel",
    )


def test_reads_many_languages_and_leaves_out_of_catalog_names():
    phrases = {
        "日本語": "ja",
        "英語": "en",
        "タイ語": "th",
        "中国語": "zh",
        "韓国語": "ko",
        "ベトナム語": "vi",
        "インドネシア語": "id",
        "スペイン語": "es",
        "フランス語": "fr",
    }
    for text, code in phrases.items():
        parsed = read_mention(text)
        assert parsed.kind == "languages", text
        assert [item for item, _surface in parsed.languages] == [code], text
        assert parsed.unsupported == (), text

    catalan = read_mention("カタルーニャ語")
    assert catalan.unsupported
    assert catalan.kind == "unsupported"
    english_and_catalan = read_mention("英語とカタルーニャ語")
    assert {code for code, _surface in english_and_catalan.languages} == {"en"}
    assert english_and_catalan.unsupported
    mixed = read_mention("英語と日本語とカタルーニャ語")
    assert {code for code, _surface in mixed.languages} == {"ja", "en"}
    assert mixed.unsupported
    invented = read_mention("エルヴ語")
    assert invented.kind == "unsupported"
    assert invented.unsupported


def test_unexpected_text_does_not_select_languages():
    for text in ("", "   ", "\n\t", "😂😂", "12345", "１２３", "???"):
        assert read_mention(text).kind == "none", repr(text)
        assert read_mention(text).languages == (), repr(text)
    assert reply_family("英語、日本語") == "ja"
    assert reply_family("英語") == "ja"
    assert reply_family("英語、日本語", ["fr", "es"]) == "en"
    assert reply_family("英語、日本語", ["zh", "en"]) == "zh"
    assert reply_family("英语和日语") == "zh"
    assert reply_family("", [], "ko") == "ko"
    assert reply_family("英語、日本語", [], "zh-tw") == "ja"


def test_unconfigured_group_sends_out_of_catalog_requests_to_propose():
    preference = _preference(("en", "English"), ("ca", "Catalan"))
    cases = (
        "英語とカタルーニャ語",
        "英語と日本語とカタルーニャ語",
        "English and Catalan",
        "add English and Catalan",
        "エルヴ語",
    )
    for text in cases:
        repo = _PromptRepo()
        line = _Line()
        pref = _Pref(preference if "エルヴ" not in text else None)
        handler = _handler(repo, line, pref=pref)

        assert handler._attempt_language_enrollment(_event(text)) is True

        assert pref.calls == [text]
        assert repo.languages == []
        assert repo.completed is None
        reply = line.last_text or ""
        assert "isn't supported" not in reply.lower()
        assert "対応していない" not in reply
    saved = _PromptRepo()
    line = _Line()
    pref = _Pref(preference)
    handler = _handler(saved, line, pref=pref)
    assert handler._attempt_language_enrollment(_event("英語とカタルーニャ語")) is True
    assert saved.prompts == ["G"]
    assert line.last_messages[0]["type"] == "template"
    assert saved.languages == []


def test_mention_on_an_unconfigured_group_also_uses_propose():
    repo = _PromptRepo()
    line = _Line()
    pref = _Pref(_preference(("en", "English"), ("ja", "Japanese"), ("ca", "Catalan")))
    handler = _handler(repo, line, pref=pref)

    assert handler._handle_command(_event("英語と日本語とカタルーニャ語"), "英語と日本語とカタルーニャ語") is True

    assert pref.calls == ["英語と日本語とカタルーニャ語"]
    assert repo.languages == []
    assert line.last_messages[0]["type"] == "template"


def test_catalog_languages_still_save_without_propose():
    repo = _PromptRepo()
    line = _Line()
    pref = _Pref()
    handler = _handler(repo, line, pref=pref)

    assert handler._attempt_language_enrollment(_event("フランス語とスペイン語")) is True

    assert pref.calls == []
    assert repo.languages == ["fr", "es"]
    assert "已收到" not in (line.last_text or "")

    repo = _PromptRepo()
    line = _Line()
    handler = _handler(repo, line, pref=_Pref())
    assert handler._attempt_language_enrollment(_event("英語")) is True
    assert repo.languages == []
    assert "受け取った" in (line.last_text or "")
    assert "已收到" not in (line.last_text or "")

    repo = _PromptRepo()
    line = _Line()
    handler = _handler(repo, line, pref=_Pref())
    for text in ("   ", "😂", "42"):
        assert handler._attempt_language_enrollment(_event(text)) is True
        assert repo.languages == []
    assert handler._attempt_language_enrollment(_event("")) is True
    assert repo.languages == []


def test_web_catalog_keeps_known_codes_and_rejects_unknown_ones():
    known = normalize_catalog_selection(["ja", "en", "th", "zh", "ko", "vi", "id", "es", "fr"], limit=5)
    assert known.error == "too_many"
    pair = normalize_catalog_selection(["vi", "id"], limit=3)
    assert pair.error is None
    assert [code for code, _name in pair.languages] == ["vi", "id"]
    unknown = normalize_catalog_selection(["en", "xx"], limit=3)
    assert unknown.error == "unsupported"
    assert unknown.unsupported == ("xx",)
    catalan = normalize_catalog_selection(["en", "ca"], limit=3)
    assert catalan.error == "unsupported"
    kept = normalize_catalog_selection(["en", "ca"], limit=3, already=["ca"])
    assert kept.error is None
    assert [code for code, _name in kept.languages] == ["en", "ca"]


def test_quick_reply_help_and_current_translate_into_group_languages():
    repo = _Repo(["fr", "es"])
    line = _Line()
    interface = _Interface()
    subs = _Subs()
    handler = PostbackHandler(
        line,
        repo,
        interface_translation=interface,
        subscription_service=subs,
    )

    def post(action):
        line.last_text = None
        line.last_messages = None
        handler.handle(
            models.PostbackEvent(
                event_type="postback",
                reply_token="token",
                timestamp=0,
                data=f"langmenu={action}",
                user_id="U",
                group_id="G",
                sender_type="group",
            )
        )

    post("help")
    help_text = line.last_text or ""
    assert "fr: Send a message in the group" in help_text
    assert "es: Send a message in the group" in help_text
    assert help_text.count("https://kotori-ai.com/api/group-languages") == 1
    assert help_text.count("Send a message in the group") == 2

    post("current")
    current_text = line.last_text or ""
    assert current_text.startswith("fr: ")
    assert "es: " in current_text
    assert "French" in current_text or "Spanish" in current_text
    targets = [call[1] for call in interface.calls]
    assert targets == [["fr", "es"], ["fr", "es"]]


def test_chat_settings_and_help_use_the_group_language():
    """paramètres / ayuda も、Quick Reply と同じくグループの設定言語で返す。"""

    marks = {
        "ja": ("今の翻訳言語", "グループにメッセージ"),
        "en": ("Current translation languages", "Send a message in the group"),
        "fr": ("fr: Current translation languages", "fr: Send a message in the group"),
        "es": ("es: Current translation languages", "es: Send a message in the group"),
        "th": ("ภาษาตอนนี้", "ส่งข้อความในกลุ่ม"),
        "my": ("my: Current translation languages", "my: Send a message in the group"),
        "ne": ("ne: Current translation languages", "ne: Send a message in the group"),
        "vi": ("vi: Current translation languages", "vi: Send a message in the group"),
        "yue": ("yue: Current translation languages", "yue: Send a message in the group"),
    }
    for code, (settings_mark, help_mark) in marks.items():
        for trigger, mark in (("paramètres", settings_mark), ("ayuda", help_mark)):
            repo = _Repo([code])
            line = _Line()
            interface = _Interface()
            handler = _handler(repo, line, pref=_Pref())
            handler._interface_translation = interface
            handler._handle_command(_event(trigger), trigger)
            reply = line.last_text or ""
            assert mark in reply, (code, trigger, reply)
            assert reply.count("group-languages") == 1
            if code in {"ja", "en", "th"}:
                assert interface.calls == [], (code, trigger)
            else:
                assert [targets for _base, targets in interface.calls] == [[code]]
            if code == "en":
                assert "fr:" not in reply
