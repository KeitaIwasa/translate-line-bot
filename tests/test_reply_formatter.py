from src.domain.models import TranslationResult
from src.presentation.reply_formatter import (
    build_translation_reply,
    format_translations,
    strip_source_echo,
    _wrap_bidi_isolate,
)


def test_build_translation_reply_wraps_bidi_isolates():
    """RTL/LTR が混在しても記号位置が崩れないよう isolate を付与する。"""

    translations = [
        TranslationResult(lang="ar", text="حسناً، ماذا سنفعل اليوم؟"),
        TranslationResult(lang="en", text="Alright, what should we do today?"),
        TranslationResult(lang="zh", text="好了，今天我們做啥也好呢？"),
    ]

    reply = build_translation_reply("さて、今日は何をしようか", translations)
    lines = reply.split("\n\n")

    # 先頭が RLE、末尾が PDF(+RLM) で囲われていることを検証
    assert lines[0].startswith("\u200F\u202B")
    assert lines[0].endswith("\u202C\u200F")

    # LTR 行は LRM + LRE/PDF + LRM
    assert lines[1].startswith("\u200E\u202A") and lines[1].endswith("\u200E")
    assert lines[2].startswith("\u200E\u202A") and lines[2].endswith("\u200E")


def test_strip_source_echo_variations():
    """原文エコーのパターンを除去できることを確認する。"""

    source = "Hello"
    # 完全一致は空になる
    assert strip_source_echo(source, "Hello") == ""
    # 区切り記号を含むパターン
    assert strip_source_echo(source, "Hello - Bonjour") == "Bonjour"
    assert strip_source_echo(source, "Hello: Salut") == "Salut"
    # 先頭に原文が残った場合の除去（括弧は残る挙動）
    assert strip_source_echo(source, "Hello (Hola)") == "(Hola)"
    # 原文が含まれない場合はそのまま
    assert strip_source_echo(source, "Ciao") == "Ciao"


def test_wrap_bidi_isolate_adds_marks():
    """RTL と LTR で前後のマークが付与されることを検証する。"""

    rtl = _wrap_bidi_isolate("مرحبا.", "ar")
    assert rtl.startswith("\u200F\u202B")
    assert rtl.endswith("\u202C\u200F")

    ltr = _wrap_bidi_isolate("Hello.", "en")
    assert ltr.startswith("\u200E\u202A")
    assert ltr.endswith("\u202C\u200E")


def test_format_translations_skips_empty_and_trims():
    """空文字は無視され、前後の空白が除去されることを確認する。"""

    translations = [
        TranslationResult(lang="en", text="  Hi "),
        TranslationResult(lang="ar", text=""),  # 空はスキップ
        TranslationResult(lang="ja", text="  こんにちは  "),
    ]

    formatted = format_translations(translations).split("\n\n")
    assert len(formatted) == 2
    assert formatted[0].endswith("Hi\u202C\u200E")
    assert formatted[1].endswith("こんにちは\u202C\u200E")


def test_build_translation_reply_drops_echo_and_preserves_others():
    """原文と同じ訳は落とし、残りはフォーマットする。"""

    original = "Hello"
    translations = [
        TranslationResult(lang="en", text="Hello"),  # エコーなので落ちる
        TranslationResult(lang="es", text="Hola"),
    ]

    reply = build_translation_reply(original, translations)
    lines = reply.split("\n\n")
    assert len(lines) == 1
    assert "Hola" in lines[0]
