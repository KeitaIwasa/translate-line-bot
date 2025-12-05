from src.domain.models import TranslationResult
from src.presentation.reply_formatter import build_translation_reply


def test_build_translation_reply_wraps_bidi_isolates():
    """RTL/LTR が混在しても記号位置が崩れないよう isolate を付与する。"""

    translations = [
        TranslationResult(lang="ar", text="حسناً، ماذا سنفعل اليوم؟"),
        TranslationResult(lang="en", text="Alright, what should we do today?"),
        TranslationResult(lang="zh", text="好了，今天我們做啥也好呢？"),
    ]

    reply = build_translation_reply("さて、今日は何をしようか", translations)
    lines = reply.split("\n\n")

    # 先頭が RLE、末尾が PDF で囲われていることを検証
    assert lines[0].startswith("\u202B") and lines[0].endswith("\u202C")

    # LTR 行は LRE/PDF + LRM
    assert lines[1].startswith("\u202A") and lines[1].endswith("\u200E")
    assert lines[2].startswith("\u202A") and lines[2].endswith("\u200E")
