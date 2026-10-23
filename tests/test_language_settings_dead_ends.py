"""言語設定の行き止まり。上限超えの全消去、区切りなし、案内文、未対応言語。"""

import pytest

from src.app.handlers.message_handler import MessageHandler
from src.presentation.rich_message import visible_reply_text
from src.domain import models
from src.domain.services.language_detection_service import LanguageDetectionService
from src.domain.services.explicit_language_reset import is_explicit_language_reset
from src.domain.services.language_mention import read_mention
from src.infra.jev_language_reader import LANGUAGE_CATALOG
from src.domain.services.language_names import JAPANESE_LANGUAGE_NAMES


class _Line:
    def __init__(self):
        self.last_text = None

    def reply_text(self, _token, text):
        self.last_text = text

    def reply_messages(self, _token, messages):
        self.last_text = visible_reply_text(messages)


class _Repo:
    def __init__(self, languages=None):
        self.languages = list(languages or [])
        self.reset_called = False
        self.enabled = []
        self.replaced = []

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

    def reset_group_language_settings(self, *_args):
        self.reset_called = True
        self.languages = []

    def add_group_languages(self, _group_id, languages):
        for code, _name in languages:
            if code not in self.languages:
                self.languages.append(code)

    def remove_group_languages(self, _group_id, codes):
        drop = {code.lower() for code in codes}
        self.languages = [code for code in self.languages if code.lower() not in drop]

    def replace_group_languages(self, _group_id, languages):
        self.replaced.append(list(languages))
        self.languages = [code for code, _name in languages]

    def apply_language_changes(self, group_id, languages_to_add, remove_codes):
        self.remove_group_languages(group_id, remove_codes)
        self.add_group_languages(group_id, languages_to_add)


class _Router:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def decide(self, _text):
        self.calls += 1
        return self.decision


def _handler(repo, router):
    return MessageHandler(
        line_client=_Line(),
        translation_service=object(),
        interface_translation=object(),
        language_detector=LanguageDetectionService(),
        language_pref_service=object(),
        command_router=router,
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="KOTORI",
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


def _reset_decision():
    return models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        instruction_language="ja",
        ack_text="リセットしました",
        explicit_reset=True,
    )


def test_all_known_languages_never_resets_and_asks_which_three():
    repo = _Repo(["en", "ja", "ko"])
    router = _Router(_reset_decision())
    handler = _handler(repo, router)
    text = "あなたの知ってる全言語を翻訳して"

    handler._handle_command(_event(text), text)

    assert repo.reset_called is False
    assert repo.languages == ["en", "ja", "ko"]
    assert router.calls == 0
    reply = handler._line.last_text or ""
    assert "最大3つまでだよ" in reply
    assert "どれにする" in reply
    assert "日本語" in reply
    assert "スワヒリ語" in reply


def test_reset_decision_for_all_languages_still_does_not_wipe():
    repo = _Repo(["en", "ja"])
    handler = _handler(repo, _Router(_reset_decision()))
    text = "あなたの知ってる全言語を翻訳して"

    handler._handle_language_settings(_event(text), _reset_decision(), text)

    assert repo.reset_called is False
    assert repo.languages == ["en", "ja"]
    assert "最大3つまでだよ" in (handler._line.last_text or "")


def test_concatenated_languages_are_a_selection_not_the_generic_menu():
    repo = _Repo(["en"])
    router = _Router(models.CommandDecision(action="unknown", instruction_language="ja", ack_text=""))
    handler = _handler(repo, router)
    text = "日本語英語韓国語中国語タイ語（に翻訳して）"

    handler._handle_command(_event(text), text)

    reply = handler._line.last_text or ""
    assert "最大3つまでだよ" in reply
    for name in ("日本語", "英語", "韓国語", "中国語", "タイ語"):
        assert name in reply
    assert "Change language settings" not in reply
    assert "言語設定の変更" not in reply
    assert repo.reset_called is False
    assert repo.languages == ["en"]


def test_settings_menu_label_shows_current_languages_and_examples():
    repo = _Repo(["en", "ja"])
    handler = _handler(repo, _Router(models.CommandDecision(action="show_languages")))
    text = "言語設定の変更"

    handler._handle_command(_event(text), text)

    reply = handler._line.last_text or ""
    assert "今の翻訳言語は" in reply
    assert "英語" in reply
    assert "日本語" in reply
    assert "タイ語を追加して" in reply
    assert "韓国語を外して" in reply
    assert repo.reset_called is False


def test_settings_menu_label_without_languages_does_not_dead_end():
    repo = _Repo([])
    handler = _handler(repo, _Router(models.CommandDecision(action="show_languages")))

    handler._handle_command(_event("言語設定の変更"), "言語設定の変更")

    reply = handler._line.last_text or ""
    assert "まだ設定されていません" in reply
    assert "メンションして" in reply
    assert "英語と日本語にして" in reply


def test_howto_answers_immediately_with_mention_examples():
    repo = _Repo(["ja"])
    router = _Router(
        models.CommandDecision(action="howto", instruction_language="ja", ack_text="使い方を教えてと送って")
    )
    handler = _handler(repo, router)

    handler._handle_command(_event("使い方"), "使い方")

    reply = handler._line.last_text or ""
    assert "使い方を教えてと送って" not in reply
    assert "メンションして" in reply
    assert "タイ語を追加して" in reply
    assert "グループにメッセージを送ると" in reply
    assert router.calls == 0
    assert repo.enabled == []


def test_howto_loop_from_the_model_is_replaced():
    repo = _Repo(["ja"])
    router = _Router(
        models.CommandDecision(action="howto", instruction_language="ja", ack_text="使い方を教えてと送って")
    )
    handler = _handler(repo, router)
    text = "もっと詳しく使い方を知りたい"

    handler._handle_command(_event(text), text)

    reply = handler._line.last_text or ""
    assert "使い方を教えてと送って" not in reply
    assert "タイ語を追加して" in reply


def test_add_over_the_limit_shows_current_languages_and_asks_what_to_swap():
    repo = _Repo(["en", "ja", "ko"])
    handler = _handler(repo, _Router(models.CommandDecision(action="unknown")))
    text = "簡体中国語とタイ語を追加"

    handler._handle_command(_event(text), text)

    reply = handler._line.last_text or ""
    assert "今は" in reply
    assert "英語" in reply and "日本語" in reply and "韓国語" in reply
    assert "簡体中国語" in reply and "タイ語" in reply
    assert "どれを外す" in reply
    assert repo.languages == ["en", "ja", "ko"]
    assert repo.reset_called is False


def test_completion_names_the_resulting_languages():
    repo = _Repo(["en"])
    handler = _handler(repo, _Router(models.CommandDecision(action="unknown")))

    handler._handle_command(_event("日本語を追加して"), "日本語を追加して")

    assert repo.languages == ["en", "ja"]
    assert handler._line.last_text == "英語・日本語になったよ"


def test_what_languages_do_you_know_returns_the_catalog():
    repo = _Repo(["en", "ja"])
    handler = _handler(repo, _Router(models.CommandDecision(action="show_languages", instruction_language="ja")))

    for text in ("何語知ってるの？", "知ってる言語すべて"):
        handler._line.last_text = None
        handler._handle_command(_event(text), text)
        reply = handler._line.last_text or ""
        assert "対応している言語はこれだよ" in reply
        assert "スワヒリ語" in reply
        assert "現在の翻訳言語は" not in reply
        assert repo.languages == ["en", "ja"]
        assert repo.reset_called is False


def test_unknown_language_is_called_out():
    repo = _Repo(["en", "ja"])
    handler = _handler(repo, _Router(models.CommandDecision(action="unknown")))

    handler._handle_command(_event("太陽語に翻訳して"), "太陽語に翻訳して")

    assert handler._line.last_text == "太陽語は対応していないよ。"
    assert repo.languages == ["en", "ja"]
    assert repo.reset_called is False


def test_supported_language_plus_unknown_keeps_the_warning():
    repo = _Repo(["ko"])
    handler = _handler(repo, _Router(models.CommandDecision(action="unknown")))

    handler._handle_command(_event("英語と太陽語"), "英語と太陽語")

    reply = handler._line.last_text or ""
    assert "太陽語は対応していないよ" in reply
    assert "英語" in reply
    assert repo.languages == ["en"]


@pytest.mark.parametrize(
    "text",
    ["😂😂", "12345", "１２３", "???", "😀"],
)
def test_emoji_and_numbers_do_not_reset_languages(text):
    repo = _Repo(["en", "ja", "ko"])
    handler = _handler(repo, _Router(_reset_decision()))

    handler._handle_command(_event(text), text)

    assert repo.reset_called is False
    assert repo.languages == ["en", "ja", "ko"]
    assert "なったよ" not in (handler._line.last_text or "")


def test_mixed_emoji_and_languages_still_parse():
    repo = _Repo(["th"])
    handler = _handler(repo, _Router(_reset_decision()))

    handler._handle_command(_event("😀 日本語とEnglish"), "😀 日本語とEnglish")

    assert repo.reset_called is False
    assert repo.languages == ["ja", "en"]
    # ラテン文字が過半数なので、完了文は英語の固定文
    reply = handler._line.last_text or ""
    assert "Japanese" in reply
    assert "English" in reply


@pytest.mark.parametrize("code,english", list(LANGUAGE_CATALOG))
def test_catalog_names_are_read_without_separators(code, english):
    japanese = JAPANESE_LANGUAGE_NAMES.get(code, "")
    assert japanese
    parsed = read_mention(f"{japanese}を追加して")
    assert parsed.kind == "languages"
    assert parsed.mode == "add"
    assert parsed.languages[0][0] == code

    glued = read_mention(japanese + "英語")
    codes = [item[0] for item in glued.languages]
    assert code in codes
    assert "en" in codes


@pytest.mark.parametrize(
    "text",
    [
        "すべての言語を削除",
        "全ての言語を消して",
        "全部の言語をリセット",
        "remove all languages",
        "delete all the languages",
        "clear all languages",
    ],
)
def test_delete_all_languages_is_a_reset_not_the_picker(text):
    parsed = read_mention(text)
    assert parsed.kind != "translate_all"
    assert is_explicit_language_reset(text)


def test_translate_all_without_a_reset_word_still_asks_which_languages():
    assert read_mention("すべての言語を翻訳して").kind == "translate_all"
    assert read_mention("all languages").kind == "translate_all"
    assert is_explicit_language_reset("すべての言語を翻訳して") is False


def test_delete_all_languages_resets_instead_of_showing_the_picker():
    repo = _Repo(["en", "ja", "ko"])
    decision = models.CommandDecision(
        action="language_settings",
        operation="reset_all",
        instruction_language="ja",
        explicit_reset=False,
    )
    handler = _handler(repo, _Router(decision))

    class _SilentTranslation:
        def translate(self, *_args, **_kwargs):
            return []

    handler._translation = _SilentTranslation()
    text = "すべての言語を削除"

    handler._handle_language_settings(_event(text), decision, text)

    assert repo.reset_called is True
    assert repo.languages == []
    assert "どれにする" not in (handler._line.last_text or "")
    assert "最大3つまで" not in (handler._line.last_text or "")


def test_odd_inputs_do_not_look_like_a_language_change():
    assert read_mention("😂😂").kind == "none"
    assert read_mention("12345").kind == "none"
    assert read_mention("日本語と英語の違いは？").kind == "none"
    assert read_mention("言語を追加したい").kind == "none"
    assert read_mention("言語設定をリセットして").kind == "none"
    assert read_mention("繁体中国語").kind == "unsupported"
    assert read_mention("エルヴ語とクリンゴーン語").unsupported


def test_confirm_template_does_not_cancel_by_itself():
    """確認テンプレートのあと、コードから自動でキャンセルは走らない。"""

    from src.domain.services.language_settings_service import LanguageSettingsService

    class _Pref:
        def analyze(self, _text):
            return models.LanguagePreference(
                supported=[
                    models.LanguageChoice(code="ja", name="Japanese"),
                    models.LanguageChoice(code="zh", name="Chinese"),
                ],
                unsupported=[],
                confirm_label="OK",
                cancel_label="Cancel",
                primary_language="ja",
            )

    class _Repo:
        def __init__(self):
            self.cancel_called = False

        def set_translation_enabled(self, *_args):
            return None

        def record_language_prompt(self, *_args):
            return None

        def try_cancel_language_prompt(self, *_args):
            self.cancel_called = True
            return True

    class _Translator:
        def translate(self, *_args, **_kwargs):
            return []

    repo = _Repo()
    service = LanguageSettingsService(
        repo,
        _Pref(),
        type("T", (), {"translate": lambda *_a, **_k: [], "_translator": _Translator()})(),
        max_group_languages=3,
    )
    bundle = service.propose(
        models.MessageEvent(
            event_type="message",
            reply_token="token",
            timestamp=0,
            text="日本語、中文",
            user_id="U",
            group_id="G",
            sender_type="group",
        ),
        max_languages=3,
    )

    assert bundle is not None
    assert bundle.messages[0]["template"]["type"] == "confirm"
    assert repo.cancel_called is False
