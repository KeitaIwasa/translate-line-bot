from src.app.handlers.message_handler import UNKNOWN_INSTRUCTION_BASE, MessageHandler
from src.domain.services.language_settings_service import LanguageSettingsService
from src.infra.gemini_mention_followup import HOWTO_SYSTEM_PROMPT, LANGUAGE_SYSTEM_PROMPT
from src.infra.gemini_translation import SYSTEM_INSTRUCTION, UI_BATCH_SYSTEM
from src.presentation.list_layout import normalize_inline_lists

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
