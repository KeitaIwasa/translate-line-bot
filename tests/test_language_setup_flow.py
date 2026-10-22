"""言語設定の完了案内、Quick Reply、片側だけの依頼、複数言語の読み取り。"""

from src.app.handlers.message_handler import MessageHandler
from src.app.handlers.postback_handler import PostbackHandler
from src.domain import models
from src.domain.services.language_detection_service import LanguageDetectionService
from src.domain.services.language_mention import (
    language_quick_replies,
    read_mention,
    setup_confirmation,
    web_settings_notice,
)
from src.group_language_handler import handle_group_language_request
from tests.test_group_language_page import _Line as _PageLine
from tests.test_group_language_page import _Repo as _PageRepo
from tests.test_group_language_page import _post, _token


class _Line:
    def __init__(self):
        self.last_text = None
        self.last_messages = None

    def reply_text(self, _token, text):
        self.last_text = text

    def reply_messages(self, _token, messages):
        self.last_messages = messages
        if messages and isinstance(messages[0], dict):
            self.last_text = messages[0].get("text")


class _Repo:
    def __init__(self, languages=None):
        self.languages = list(languages or [])
        self.completed = None
        self.enabled = []
        self.reset_called = False

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
            entitlement_plan="free",
        )

    def set_translation_enabled(self, _group_id, enabled):
        self.enabled.append(enabled)
        return None

    def try_complete_group_languages(self, _group_id, languages):
        self.completed = list(languages)
        self.languages = [code for code, _name in languages]
        return True

    def replace_group_languages(self, _group_id, languages):
        self.languages = [code for code, _name in languages]

    def add_group_languages(self, _group_id, languages):
        for code, _name in languages:
            if code not in self.languages:
                self.languages.append(code)

    def remove_group_languages(self, _group_id, codes):
        drop = {code.lower() for code in codes}
        self.languages = [code for code in self.languages if code.lower() not in drop]

    def reset_group_language_settings(self, *_args):
        self.reset_called = True
        self.languages = []


class _Subs:
    def __init__(self):
        self.calls = []

    def create_language_settings_url(self, group_id, *, family=""):
        self.calls.append((group_id, family))
        n = len(self.calls)
        suffix = f"&ui={family}" if family else ""
        return f"https://kotori-ai.com/api/group-languages?openExternalBrowser=1&st=tok{n}{suffix}"


class _Pref:
    def __init__(self):
        self.calls = []

    def analyze(self, text):
        self.calls.append(text)
        return None


def _handler(repo, line, subs=None, pref=None):
    return MessageHandler(
        line_client=line,
        translation_service=object(),
        interface_translation=object(),
        language_detector=LanguageDetectionService(),
        language_pref_service=pref or _Pref(),
        command_router=object(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="KOTORI",
        subscription_service=subs or _Subs(),
    )


def _event(text):
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text=text,
        user_id="U",
        group_id="G",
        sender_type="group",
    )


def test_cross_script_phrases_read_every_language():
    cases = {
        "한국어를 일본어로 상호 번역해줘": {"ko", "ja"},
        "德語翻譯成中文": {"de", "zh"},
        "韓国語⇔日本語": {"ko", "ja"},
        "English Japanese Italian": {"en", "ja", "it"},
        "中文, 日本語": {"zh", "ja"},
        "독일어를 중국어로 번역해줘": {"de", "zh"},
        "español y français": {"es", "fr"},
        "ภาษาไทยและ英語": {"th", "en"},
        "日本語↔英語": {"ja", "en"},
    }
    for text, codes in cases.items():
        parsed = read_mention(text)
        assert parsed.kind == "languages", text
        assert {code for code, _surface in parsed.languages} == codes, text


def test_unexpected_inputs_do_not_become_a_language_pair():
    assert read_mention("😂😂").kind == "none"
    assert read_mention("12345").kind == "none"
    assert read_mention("１２３").kind == "none"
    assert read_mention("???").kind == "none"
    assert read_mention("エルヴ語").kind == "unsupported"
    assert read_mention("クリンゴーン語とエルフ語").kind == "unsupported"
    mixed = read_mention("英語と태양어")
    assert mixed.kind == "none" or mixed.unsupported or mixed.kind == "languages"
    one = read_mention("😂英語")
    assert one.kind == "languages"
    assert [code for code, _surface in one.languages] == ["en"]
    both = read_mention("123 日本語 英語")
    assert {code for code, _surface in both.languages} == {"ja", "en"}


def test_menu_words_in_several_languages():
    for text in ("設定", "settings", "language", "Language", "언어", "设置", "ภาษา", "ayuda"):
        assert read_mention(text).kind in {"settings_help", "howto"}, text
    for text in ("help", "/help", "ヘルプ", "도움말", "帮助", "Hilfe"):
        assert read_mention(text).kind == "howto", text


def test_setup_confirmation_translates_known_families_and_falls_back_to_english():
    text = setup_confirmation(
        [("ja", "Japanese"), ("it", "Italian")],
        url="https://kotori-ai.com/api/group-languages?st=fresh",
    )
    assert "日本語・イタリア語になったよ" in text
    assert "Japanese and Italian have been set as the translation languages." in text
    assert text.count("https://kotori-ai.com/api/group-languages?st=fresh") == 1
    assert "設定" in text
    assert "anytime" in text.lower()
    # イタリア語の静的訳は無いので、英語は一度だけ
    assert text.lower().count("have been set") == 1


class _Translated:
    def __init__(self, lang, text):
        self.lang = lang
        self.text = text


def _per_language(base, targets):
    return [_Translated(code, f"{code}: {base.splitlines()[0]}") for code in targets]


def test_web_notice_and_setup_copy_translate_every_group_language():
    pairs = [("fr", "French"), ("es", "Spanish"), ("de", "German")]
    notice = web_settings_notice(pairs, translate=_per_language)
    assert "fr: Updated from the settings page" in notice
    assert "es: Updated from the settings page" in notice
    assert "de: Updated from the settings page" in notice
    assert notice.count("Updated from the settings page") == 3

    url = "https://kotori-ai.com/api/group-languages?st=fresh"
    confirmation = setup_confirmation(pairs, url=url, translate=_per_language)
    assert "fr: French, Spanish, and German have been set" in confirmation
    assert "es: French, Spanish, and German have been set" in confirmation
    assert "de: French, Spanish, and German have been set" in confirmation
    assert confirmation.count(url) == 1
    assert confirmation.lower().count("have been set") == 3


def test_translation_failure_uses_static_text_then_english_once():
    def broken(_base, _targets):
        raise RuntimeError("translator down")

    french = web_settings_notice(
        [("fr", "French"), ("es", "Spanish"), ("de", "German")],
        translate=broken,
    )
    assert french == "Updated from the settings page: French, Spanish, and German."

    # 翻訳結果が原文と同じときも失敗として、固定文へ戻す
    def echo(base, targets):
        return [_Translated(code, base) for code in targets]

    japanese = web_settings_notice([("ja", "Japanese"), ("fr", "French")], translate=echo)
    assert "設定ページから更新したよ" in japanese
    assert "Updated from the settings page" in japanese
    assert japanese.count("Updated from the settings page") == 1

    url = "https://kotori-ai.com/api/group-languages?st=fallback"
    confirmation = setup_confirmation(
        [("ja", "Japanese"), ("fr", "French")],
        url=url,
        translate=lambda *_args, **_kwargs: [],
    )
    assert "日本語・フランス語になったよ" in confirmation
    assert "Japanese and French have been set as the translation languages." in confirmation
    assert confirmation.lower().count("have been set") == 1
    assert confirmation.count(url) == 1

    # 翻訳できた言語名（fr-FR）は、グループの fr に割り当てる
    def regional(base, _targets):
        return [_Translated("fr-FR", f"REGIONAL {base.splitlines()[0]}")]

    regional_notice = web_settings_notice([("fr", "French")], translate=regional)
    assert regional_notice.startswith("REGIONAL Updated from the settings page")
    assert regional_notice.count("Updated from the settings page") == 1


def test_quick_reply_items_fit_line_limits():
    items = language_quick_replies("ja", "https://kotori-ai.com/api/group-languages?st=abc")
    assert items[0]["action"]["type"] == "uri"
    assert items[1]["action"]["data"] == "langmenu=current"
    assert items[2]["action"]["data"] == "langmenu=help"
    for item in items:
        label = item["action"]["label"]
        assert 1 <= len(label) <= 20
    assert language_quick_replies("en", "https://example.com/" + ("a" * 1200))[0]["action"]["type"] == "postback"


def test_plan_limit_rejects_add_on_first_setup_existing_group_and_web():
    repo = _Repo()
    line = _Line()
    handler = _handler(repo, line)

    assert handler._attempt_language_enrollment(_event("add English Japanese Chinese Korean")) is True

    reply = line.last_text or ""
    assert "up to 3" in reply
    assert "English" in reply
    assert "Korean" in reply
    assert repo.languages == []
    assert repo.completed is None

    repo.languages = ["en", "ja", "zh"]
    assert handler._handle_command(_event("add Korean"), "add Korean") is True
    assert repo.languages == ["en", "ja", "zh"]
    again = line.last_text or ""
    assert "up to 3" in again
    assert "swap out" in again

    page = _PageRepo([("en", "English")])
    response = _post(page, _token(), ["ja", "en", "zh", "ko"])
    assert response["statusCode"] == 400
    assert "up to 3" in response["body"]
    assert page.replaced == []
    assert page.records == [("en", "English")]


def test_enrollment_saves_a_pair_once_and_includes_a_fresh_link():
    repo = _Repo()
    line = _Line()
    subs = _Subs()
    pref = _Pref()
    handler = _handler(repo, line, subs, pref)

    assert handler._attempt_language_enrollment(_event("中文, 日本語")) is True

    assert pref.calls == []
    assert repo.languages == ["zh", "ja"]
    assert True in repo.enabled
    reply = line.last_text or ""
    assert "中文" in reply and "日本語" in reply
    assert "anytime" in reply.lower() or "いつでも" in reply
    assert "設定" in reply
    assert "st=tok1" in reply
    assert line.last_messages is None or line.last_messages[0].get("type") != "template"


def test_enrollment_asks_for_the_other_language_and_ignores_noise():
    repo = _Repo()
    line = _Line()
    pref = _Pref()
    handler = _handler(repo, line, _Subs(), pref)

    assert handler._attempt_language_enrollment(_event("英語")) is True
    assert repo.completed is None
    assert repo.languages == []
    reply = line.last_text or ""
    assert "your language" in reply.lower() or "言語" in reply or "语言" in reply or "언어" in reply
    assert pref.calls == []

    assert handler._attempt_language_enrollment(_event("😂😂")) is True
    assert repo.languages == []
    assert pref.calls == []
    assert "couldn't tell" in (line.last_text or "").lower() or "読み取れなかった" in (line.last_text or "")

    assert handler._attempt_language_enrollment(_event("12345")) is True
    assert repo.languages == []


def test_mention_settings_sends_quick_replies_and_a_new_token_each_time():
    repo = _Repo(["ja", "en"])
    line = _Line()
    subs = _Subs()
    handler = _handler(repo, line, subs)

    for text in ("設定", "help", "/help", "language", "Language"):
        line.last_messages = None
        handler._handle_command(_event(text), text)
        messages = line.last_messages or []
        assert messages, text
        actions = [item["action"]["type"] for item in messages[0]["quickReply"]["items"]]
        assert "uri" in actions, text
        assert "postback" in actions, text
        assert "group-languages" in (line.last_text or "")

    assert len(subs.calls) >= 2
    urls = []
    handler._handle_command(_event("設定"), "設定")
    urls.append(line.last_text)
    handler._handle_command(_event("設定"), "設定")
    urls.append(line.last_text)
    assert "st=tok" in urls[0] and "st=tok" in urls[1]
    assert urls[0] != urls[1]


def test_replacing_with_one_language_does_not_save():
    repo = _Repo(["ja", "ko"])
    line = _Line()
    handler = _handler(repo, line)
    decision = models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        languages_to_add=[models.LanguageChoice(code="en", name="English")],
        instruction_language="ja",
    )

    handler._handle_language_settings(_event("英語だけにして"), decision, "英語だけにして")

    assert repo.languages == ["ja", "ko"]
    assert "あなたの言語も" in (line.last_text or "")


def test_removing_down_to_one_language_does_not_save():
    repo = _Repo(["ja", "en"])
    line = _Line()
    handler = _handler(repo, line)
    decision = models.CommandDecision(
        action="language_settings",
        operation="remove",
        languages_to_remove=[models.LanguageChoice(code="en", name="English")],
        instruction_language="ja",
    )

    handler._handle_language_settings(_event("英語を外して"), decision, "英語を外して")

    assert repo.languages == ["ja", "en"]
    assert "あなたの言語も" in (line.last_text or "")


def test_postback_menu_returns_current_languages_for_any_member_tap():
    repo = _Repo(["ja", "en"])
    line = _Line()
    subs = _Subs()
    handler = PostbackHandler(line, repo, subscription_service=subs)
    event = models.PostbackEvent(
        event_type="postback",
        reply_token="token",
        timestamp=0,
        data="langmenu=current",
        user_id="another-member",
        group_id="G",
        sender_type="group",
    )

    handler.handle(event)

    assert "日本語" in (line.last_text or "")
    assert "英語" in (line.last_text or "")
    assert line.last_messages[0]["quickReply"]["items"][0]["action"]["type"] == "uri"
    assert subs.calls[-1][0] == "G"


def test_page_rejects_one_language_and_ignores_who_submitted():
    repo = _PageRepo([("en", "English"), ("ja", "Japanese")])
    response = _post(repo, _token(), ["ja"], extra=[("user_id", "stranger")])
    assert response["statusCode"] == 400
    assert "2" in response["body"] or "二つ" in response["body"] or "兩" in response["body"] or "두" in response["body"]
    assert repo.replaced == []

    line = _PageLine()
    saved = _post(repo, _token("G-members"), ["ko", "ja"], line=line, extra=[("user_id", "someone-else")])
    assert saved["statusCode"] == 200
    assert repo.replaced[0][0] == "G-members"
    assert [code for code, _name in repo.replaced[0][1]] == ["ko", "ja"]
    assert line.pushed


def test_page_get_does_not_require_a_user():
    repo = _PageRepo([("ja", "Japanese")])
    from src.group_language_handler import handle_group_language_request as handle
    from tests.test_group_language_page import _event

    response = handle(_event(token=_token()), repo=repo, line=_PageLine(), secret="secret")
    assert response["statusCode"] == 200
    assert "member" in response["body"].lower() or "メンバー" in response["body"]
