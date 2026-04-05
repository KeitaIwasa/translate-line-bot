from src.app.handlers.message_handler import MessageHandler
from src.domain import models


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


def _event(
    text: str,
    *,
    destination: str | None = None,
    mentionees: list[models.Mentionee] | None = None,
) -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G1",
        user_id="U1",
        sender_type="group",
        text=text,
        destination=destination,
        mentionees=mentionees or [],
        timestamp=1700000000000,
    )


def test_command_requires_at_prefix():
    handler = _build_handler()

    assert handler._extract_command_text(_event("bot help")) is None
    assert handler._extract_command_text(_event("@bot help")) == "help"


def test_command_allows_whitespace_after_at():
    handler = _build_handler()

    assert handler._extract_command_text(_event("@  bot stop")) == "stop"


def test_command_detects_mention_in_sentence():
    handler = _build_handler()

    assert handler._extract_command_text(_event("hello @bot stop")) == "hello stop"


def test_command_detects_mention_only():
    handler = _build_handler()

    # メンションのみでも None ではなく空文字列を返す（コマンド扱い）
    assert handler._extract_command_text(_event("@bot")) == ""


def test_command_detects_localized_mention_via_is_self_metadata():
    handler = _build_handler(bot_name="KOTORI - AI翻訳")
    text = "@通訳AI - การตีความ AI アップグレード"
    mention = models.Mentionee(index=0, length=20, mention_type="user", user_id="BOT", is_self=True)

    assert handler._extract_command_text(_event(text, destination="BOT", mentionees=[mention])) == "アップグレード"


def test_command_detects_localized_mention_via_destination_user_id():
    handler = _build_handler(bot_name="KOTORI - AI翻訳")
    text = "@通訳AI - การตีความ AI プラン変更"
    mention = models.Mentionee(index=0, length=20, mention_type="user", user_id="BOT", is_self=False)

    assert handler._extract_command_text(_event(text, destination="BOT", mentionees=[mention])) == "プラン変更"


def test_command_metadata_keeps_other_mentions():
    handler = _build_handler(bot_name="KOTORI - AI翻訳")
    text = "@alice @通訳AI - การตีความ AI stop"
    mentionees = [
        models.Mentionee(index=0, length=6, mention_type="user", user_id="USER1", is_self=False),
        models.Mentionee(index=7, length=20, mention_type="user", user_id="BOT", is_self=True),
    ]

    assert handler._extract_command_text(_event(text, destination="BOT", mentionees=mentionees)) == "@alice stop"
