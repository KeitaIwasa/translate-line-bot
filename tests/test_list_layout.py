from src.app.handlers.message_handler import UNKNOWN_INSTRUCTION_BASE, MessageHandler
from src.domain import models
from src.domain.services.language_settings_service import LanguageSettingsService
from src.domain.services.quota_service import QuotaDecision
from src.domain.services.translation_flow_service import TranslationFlowResult
from src.infra.gemini_mention_followup import HOWTO_SYSTEM_PROMPT, LANGUAGE_SYSTEM_PROMPT
from src.infra.gemini_translation import SYSTEM_INSTRUCTION, UI_BATCH_SYSTEM
from src.presentation.list_layout import normalize_inline_lists, normalize_translated_user_text

_SCREENSHOT = (
    "1. Add KOTORI as a friend on LINE. 2. Invite KOTORI to the LINE group you want to use. "
    "3. Enter the languages you want to translate in the group (e.g., Japanese, English, Thai). "
    "4. Press Complete on the confirmation template. "
    "5. Conversations in that group will be automatically translated."
)

_SCREENSHOT_EXPECTED = (
    "1. Add KOTORI as a friend on LINE.\n"
    "2. Invite KOTORI to the LINE group you want to use.\n"
    "3. Enter the languages you want to translate in the group (e.g., Japanese, English, Thai).\n"
    "4. Press Complete on the confirmation template.\n"
    "5. Conversations in that group will be automatically translated."
)

_ALREADY_FORMATTED = (
    "After setting your languages, chat in any language.\n"
    "\n"
    "1. Add KOTORI as a friend on LINE.\n"
    "2. Invite KOTORI to the LINE group you want to use.\n"
    "3. Enter the languages you want to translate in the group.\n"
    "\n"
    "Conversations in that group will be translated automatically."
)


def test_screenshot_numbered_steps_become_one_item_per_line():
    assert normalize_inline_lists(_SCREENSHOT) == _SCREENSHOT_EXPECTED


def test_already_formatted_reply_stays_unchanged():
    assert normalize_inline_lists(_ALREADY_FORMATTED) == _ALREADY_FORMATTED
    assert normalize_inline_lists(UNKNOWN_INSTRUCTION_BASE) == UNKNOWN_INSTRUCTION_BASE
    second = normalize_inline_lists(_SCREENSHOT_EXPECTED)
    assert second == _SCREENSHOT_EXPECTED


def test_inline_lists_split_across_languages():
    cases = {
        "ja": (
            "1. LINEで友だちに追加します。2. 使いたいグループに招待します。3. 翻訳する言語を入力します。",
            "1. LINEで友だちに追加します。\n2. 使いたいグループに招待します。\n3. 翻訳する言語を入力します。",
        ),
        "ko": (
            "1. LINE에서 친구로 추가합니다. 2. 사용할 그룹에 초대합니다. 3. 번역할 언어를 입력합니다.",
            "1. LINE에서 친구로 추가합니다.\n2. 사용할 그룹에 초대합니다.\n3. 번역할 언어를 입력합니다.",
        ),
        "th": (
            "1. เพิ่ม KOTORI เป็นเพื่อนใน LINE 2. เชิญเข้ากลุ่มที่ต้องการใช้ 3. ใส่ภาษาที่ต้องการแปล",
            "1. เพิ่ม KOTORI เป็นเพื่อนใน LINE\n2. เชิญเข้ากลุ่มที่ต้องการใช้\n3. ใส่ภาษาที่ต้องการแปล",
        ),
        "ar": (
            "1. أضف كوتوري كصديق في LINE. 2. ادعُ البوت إلى المجموعة. 3. أدخل اللغات.",
            "1. أضف كوتوري كصديق في LINE.\n2. ادعُ البوت إلى المجموعة.\n3. أدخل اللغات.",
        ),
        "ar-indic": (
            "١. أضف كوتوري كصديق. ٢. ادعُ البوت إلى المجموعة. ٣. أدخل اللغات.",
            "١. أضف كوتوري كصديق.\n٢. ادعُ البوت إلى المجموعة.\n٣. أدخل اللغات.",
        ),
        "ja-fullwidth": (
            "１．友だち追加\u3000２．グループに招待\u3000３．言語を入力",
            "１．友だち追加\n２．グループに招待\n３．言語を入力",
        ),
        "ko-bullets": (
            "명령: - 언어 설정 변경 - 사용법 - 번역 중지",
            "명령:\n- 언어 설정 변경\n- 사용법\n- 번역 중지",
        ),
        "th-bullets": (
            "คำสั่ง: - เปลี่ยนภาษา - วิธีใช้ - หยุดการแปล",
            "คำสั่ง:\n- เปลี่ยนภาษา\n- วิธีใช้\n- หยุดการแปล",
        ),
        "ja-bullets": (
            "次のコマンド: - 言語設定の変更 - 使い方 - 翻訳停止",
            "次のコマンド:\n- 言語設定の変更\n- 使い方\n- 翻訳停止",
        ),
    }
    for name, (source, expected) in cases.items():
        assert normalize_inline_lists(source) == expected, name


def test_spaced_hyphens_are_not_inline_bullets():
    assert normalize_inline_lists("Tokyo - Osaka - Kyoto") == "Tokyo - Osaka - Kyoto"
    assert normalize_inline_lists("10 - 20") == "10 - 20"
    assert normalize_inline_lists("The fare is 10 - 20 yen.") == "The fare is 10 - 20 yen."


def test_bullets_split_only_when_the_line_starts_a_list():
    assert normalize_inline_lists("- Tokyo - Osaka - Kyoto") == "- Tokyo\n- Osaka\n- Kyoto"
    assert normalize_inline_lists("Cities: - Tokyo - Osaka - Kyoto") == (
        "Cities:\n- Tokyo\n- Osaka\n- Kyoto"
    )
    assert normalize_inline_lists("Visit Tokyo - Osaka - Kyoto this week.") == (
        "Visit Tokyo - Osaka - Kyoto this week."
    )


def test_user_translation_keeps_dashes_unless_source_has_line_breaks():
    translated = "来週は Tokyo - Osaka - Kyoto を回る。所要は 10 - 20 時間です。"
    assert normalize_translated_user_text("来週は東京 - 大阪 - 京都を回る。", translated) == translated
    collapsed = "1. 友だち追加 2. グループに招待"
    source = "1. Add a friend.\n2. Invite the bot."
    assert normalize_translated_user_text("友だちを追加して招待する", collapsed) == collapsed
    assert normalize_translated_user_text(source, collapsed) == "1. 友だち追加\n2. グループに招待"


class _RecordingLine:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def reply_text(self, _reply_token: str, text: str) -> None:
        self.texts.append(text)


class _Dummy:
    pass


def _handler(line: _RecordingLine) -> MessageHandler:
    return MessageHandler(
        line_client=line,
        translation_service=_Dummy(),
        interface_translation=_Dummy(),
        language_detector=_Dummy(),
        language_pref_service=_Dummy(),
        command_router=_Dummy(),
        repo=_Dummy(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
    )


def _group_event(text: str) -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G1",
        user_id="U1",
        sender_type="group",
        text=text,
        timestamp=1700000000000,
    )


def test_translated_user_sentence_with_dashes_is_sent_unchanged():
    line = _RecordingLine()
    handler = _handler(line)
    sentence = "Let's ride Tokyo - Osaka - Kyoto. Budget is 10 - 20 thousand yen."
    handler._reply_translation_result(
        event=_group_event("東京 - 大阪 - 京都に乗ろう。予算は10 - 20だ。"),
        flow=TranslationFlowResult(
            decision=QuotaDecision(True, False, False, 1, 50, "2026-10", "free"),
            reply_text=sentence,
        ),
        language_limit=5,
        removed_languages=[],
        limit=50,
        plan_key="free",
        period_end=None,
    )
    assert line.texts == [sentence]


def test_bot_reply_still_splits_numbered_steps():
    line = _RecordingLine()
    handler = _handler(line)
    handler._reply_text(
        _group_event("how do I use this bot?"),
        "1. Add a friend. 2. Invite the bot.",
    )
    assert line.texts == ["1. Add a friend.\n2. Invite the bot."]


def test_non_list_text_urls_and_numbers_stay_unchanged():
    samples = [
        "Sorry, I can't answer that question. Please contact us here: https://kotori-ai.com/contact.html",
        "Version 1.2.3 is live. See https://kotori-ai.com/v1.2/notes for details.",
        "The price is 480 yen (about 3.14 USD).",
        "You can set up to 5. Please specify 3 or fewer.",
        "I bought 1. She bought 2. Done.",
        "Plan: Free (no subscription)",
        "Do you want to enable translation for Japanese, English, and Thai?",
        "10 - 20 yen, or about 30 - 40 yen.",
        "Please contact us - the administrator will reply.",
        "日本語とEnglishとไทยを追加しました。",
    ]
    for sample in samples:
        assert normalize_inline_lists(sample) == sample


def test_intro_sentence_is_separated_from_the_list():
    source = "Here is how to use KOTORI. 1. Add a friend. 2. Invite the bot."
    assert normalize_inline_lists(source) == (
        "Here is how to use KOTORI.\n1. Add a friend.\n2. Invite the bot."
    )


def test_plan_and_language_confirmation_templates_keep_plain_sentences():
    plan = "Plan: Free (no subscription)"
    assert MessageHandler._normalize_template_text(plan) == plan
    confirmation = "Do you want to enable translation for Japanese and English?"
    assert LanguageSettingsService._normalize_template_text(confirmation) == confirmation
    listed = LanguageSettingsService._normalize_template_text(
        "確認する言語: 1. 日本語 2. English 3. ไทย"
    )
    assert listed == "確認する言語:\n1. 日本語\n2. English\n3. ไทย"


def test_prompts_keep_list_line_breaks():
    assert "own line" in HOWTO_SYSTEM_PROMPT
    assert "1. ... 2. ..." in HOWTO_SYSTEM_PROMPT
    assert "own line" in LANGUAGE_SYSTEM_PROMPT
    assert "Preserve every line break" in SYSTEM_INSTRUCTION
    assert "one item per line" in SYSTEM_INSTRUCTION
    assert "Preserve line breaks" in UI_BATCH_SYSTEM
    assert "one numbered or bulleted item per line" in UI_BATCH_SYSTEM
