"""チャットの言語操作は、向き・未設定・返信言語を推測で埋めない。"""

import pytest

from src.domain.services.language_mention import read_mention, setup_confirmation
from tests.test_language_setup_flow import _Line, _Pref, _Repo, _Subs, _event, _handler


@pytest.mark.parametrize(
    ("text", "source", "target"),
    [
        ("英語を中国語に変えて", "en", "zh"),
        ("中国語に英語を置き換えて", "en", "zh"),
        ("中国語を英語に置き換えて", "zh", "en"),
        ("replace English with Chinese", "en", "zh"),
        ("change English to Chinese", "en", "zh"),
        ("replace Chinese with English", "zh", "en"),
        ("change Chinese to English", "zh", "en"),
        ("把英语换成中文", "en", "zh"),
        ("把中文换成英语", "zh", "en"),
        ("영어를 중국어로 바꿔", "en", "zh"),
        ("중국어로 영어를 교체", "en", "zh"),
        ("중국어를 영어로 바꿔", "zh", "en"),
    ],
)
def test_directed_replace_keeps_from_and_to(text, source, target):
    parsed = read_mention(text)
    assert parsed.kind == "languages", text
    assert parsed.mode == "swap"
    assert parsed.swap_from == source
    assert parsed.swap_to == target


def test_ambiguous_replace_does_not_guess_roles():
    parsed = read_mention("英語と日本語を中国語に変えて")
    assert parsed.kind == "swap_unclear"
    assert parsed.swap_from == ""
    assert parsed.swap_to == ""


def test_replace_in_chat_uses_roles_and_does_not_invert():
    repo = _Repo(["en", "ja"])
    line = _Line()
    handler = _handler(repo, line)

    assert handler._handle_command(_event("英語を中国語に変えて"), "英語を中国語に変えて") is True
    assert repo.languages == ["ja", "zh"]
    assert line.last_text

    repo.languages = ["en", "ja"]
    assert handler._handle_command(_event("中国語を英語に置き換えて"), "中国語を英語に置き換えて") is True
    assert repo.languages == ["en", "ja"]
    assert "どちらを外して" in (line.last_text or "")


def test_remove_of_an_unset_language_replies_with_current_languages_and_a_link():
    cases = (
        ("英語を外して", "設定されていない"),
        ("韓国語を外して", "設定されていない"),
        ("remove French", "isn't set"),
    )
    for text, expected in cases:
        repo = _Repo(["ja", "zh"])
        line = _Line()
        handler = _handler(repo, line)

        assert handler._handle_command(_event(text), text) is True

        assert repo.languages == ["ja", "zh"]
        reply = line.last_text or ""
        assert reply.strip()
        assert expected in reply
        assert "group-languages" in reply


def test_plain_message_with_no_languages_asks_for_setup_instead_of_a_failure():
    repo = _Repo()
    line = _Line()
    pref = _Pref()
    handler = _handler(repo, line, pref=pref)

    assert handler._attempt_language_enrollment(_event("hello")) is True

    reply = line.last_text or ""
    assert "失敗しました" not in reply
    assert "Tell me the languages" in reply
    assert "group-languages" in reply
    assert pref.calls == ["hello"]


def test_settings_reply_language_follows_the_message():
    empty = _Repo()
    line = _Line()
    subs = _Subs()
    handler = _handler(empty, line, subs)
    handler._handle_command(_event("設定"), "設定")
    reply = line.last_text or ""
    assert "翻訳言語はまだ設定されていません" in reply
    assert "还没有" not in reply
    assert subs.calls[-1][1] == "ja"
    assert "ui=ja" in reply

    group = _Repo(["en", "ja"])
    line = _Line()
    subs = _Subs()
    handler = _handler(group, line, subs)
    handler._handle_command(_event("语言"), "语言")
    reply = line.last_text or ""
    assert "要改的话" in reply
    assert "今の翻訳言語" not in reply
    assert subs.calls[-1][1] == "zh"
    assert "ui=zh" in reply


def test_fixed_copy_keeps_static_text_when_translation_is_wrong():
    def mistranslate(_base, targets):
        return [type("T", (), {"lang": code, "text": "English and Japanese and「설정」을 보내새로운"})() for code in targets]

    text = setup_confirmation(
        [("ja", "日本語"), ("zh", "中文"), ("ko", "한국어")],
        url="https://kotori-ai.com/api/group-languages?st=fresh",
        translate=mistranslate,
    )

    assert "已设置为日语、中文、韩语" in text
    assert "영어" in text or "일본어" in text
    assert "보내 줘" in text
    assert "English and" not in text
    assert "보내새로운" not in text
