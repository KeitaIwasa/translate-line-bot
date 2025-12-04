from src.app.handlers.message_handler import MessageHandler


class _Dummy:
    """Placeholder dependency; methods are never invoked in these tests."""


def _build_handler(bot_name: str = "bot") -> MessageHandler:
    return MessageHandler(
        line_client=_Dummy(),
        translation_service=_Dummy(),
        interface_translation=_Dummy(),
        language_detector=_Dummy(),
        language_pref_service=_Dummy(),
        command_router=_Dummy(),
        repo=_Dummy(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name=bot_name,
    )


def test_command_requires_at_prefix():
    handler = _build_handler()

    assert handler._extract_command_text("bot help") is None
    assert handler._extract_command_text("@bot help") == "help"


def test_command_allows_whitespace_after_at():
    handler = _build_handler()

    assert handler._extract_command_text("@  bot stop") == "stop"


def test_command_detects_mention_in_sentence():
    handler = _build_handler()

    assert handler._extract_command_text("hello @bot stop") == "hello stop"
