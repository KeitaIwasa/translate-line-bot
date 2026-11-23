from src.app.subscription_texts import (
    SUBS_CANCEL_LABEL,
    SUBS_MENU_TEXT,
    SUBS_MENU_TITLE,
    SUBS_NO_LABEL,
    SUBS_UPGRADE_LABEL,
    SUBS_VIEW_LABEL,
    SUBS_YES_LABEL,
)
from src.app.ui_strings import (
    UI_LANGUAGE_CODES,
    _STRINGS,
    normalize_ui_language,
    ui_text,
    ui_text_for_instruction,
    ui_text_multilingual,
)

# LINE の buttons は 20、confirm のラベルは呼び出し側が 12 で切る。
_BUTTON_KEYS = (SUBS_VIEW_LABEL, SUBS_CANCEL_LABEL, SUBS_UPGRADE_LABEL)
_CONFIRM_KEYS = (SUBS_YES_LABEL, SUBS_NO_LABEL)


def test_ui_languages_cover_product_locales():
    assert {"en", "ja", "zh-TW", "th"} <= set(UI_LANGUAGE_CODES)
    for table in _STRINGS.values():
        assert set(table) == set(UI_LANGUAGE_CODES)
        assert table["en"]


def test_japanese_subscription_title_is_stable():
    assert ui_text(SUBS_MENU_TITLE, "ja") == "プラン"
    assert ui_text(SUBS_MENU_TITLE, "ja-JP") == "プラン"
    assert ui_text(SUBS_MENU_TITLE, "JA") == "プラン"
    assert "さぶす" not in ui_text(SUBS_MENU_TITLE, "ja")
    assert "サブスク" not in ui_text(SUBS_MENU_TITLE, "ja")


def test_unknown_language_falls_back_to_english():
    assert normalize_ui_language("fr") == "en"
    assert normalize_ui_language("ko") == "en"
    assert ui_text(SUBS_MENU_TITLE, "es") == SUBS_MENU_TITLE
    assert ui_text(SUBS_MENU_TEXT, None) == SUBS_MENU_TEXT
    assert ui_text("not a prepared string", "ja") == "not a prepared string"


def test_language_aliases():
    assert normalize_ui_language("zh_TW") == "zh-TW"
    assert normalize_ui_language("zh-Hant") == "zh-TW"
    assert normalize_ui_language("zh-CN") == "zh-CN"
    assert normalize_ui_language("zh") == "zh-CN"
    assert normalize_ui_language("th_TH") == "th"
    assert normalize_ui_language("en-US") == "en"
    assert ui_text(SUBS_VIEW_LABEL, "zh-Hant") == "管理帳單"
    assert ui_text(SUBS_VIEW_LABEL, "zh-Hans") == "管理账单"


def test_instruction_language_falls_back_to_group_language():
    assert ui_text_for_instruction(SUBS_MENU_TITLE, "", ["en", "th"]) == "แพ็กเกจ"
    assert ui_text_for_instruction(SUBS_MENU_TITLE, "ja", ["th"]) == "プラン"


def test_multilingual_lines_follow_group_languages():
    text = ui_text_multilingual(SUBS_MENU_TITLE, ["ja", "th", "fr"])
    assert text == "プラン\n\nแพ็กเกจ\n\nSubscription"
    assert ui_text_multilingual(SUBS_MENU_TITLE, []) == "Subscription"


def test_button_labels_fit_line_limits():
    for key in _BUTTON_KEYS:
        for lang in UI_LANGUAGE_CODES:
            assert len(ui_text(key, lang)) <= 20
    for key in _CONFIRM_KEYS:
        for lang in UI_LANGUAGE_CODES:
            assert len(ui_text(key, lang)) <= 12
    for lang in UI_LANGUAGE_CODES:
        assert len(ui_text(SUBS_MENU_TITLE, lang)) <= 40
