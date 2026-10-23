"""設定案内・参加案内・コマンド返信を、共通の Flex カードにする。

本文の原文は英語（または言語ごとの固定文）のまま保ち、リンクだけボタンに移す。
ボタンラベルは英語を基準に、ja / zh / ko / th / ar / he へ固定訳する。
それ以外の言語は英語ラベルのままにし、本文の翻訳は呼び出し側が行う。
"""

from __future__ import annotations

from .list_layout import normalize_inline_lists
from .rich_message import GuidanceMessage, RichButton, split_urls

# 英語ラベルを先に書き、同じキーの翻訳を続ける。クイックリプライの表示は 20 文字以内。
_LABELS: dict[str, tuple[tuple[str, str], ...]] = {
    "how_to_use": (
        ("en", "How to use"),
        ("ja", "使い方"),
        ("zh", "使用方法"),
        ("ko", "사용법"),
        ("th", "วิธีใช้"),
        ("ar", "طريقة الاستخدام"),
        ("he", "איך להשתמש"),
    ),
    "language_settings": (
        ("en", "Language settings"),
        ("ja", "言語設定"),
        ("zh", "语言设置"),
        ("ko", "언어 설정"),
        ("th", "ตั้งค่าภาษา"),
        ("ar", "إعدادات اللغة"),
        ("he", "הגדרות שפה"),
    ),
    "commands": (
        ("en", "Commands"),
        ("ja", "コマンド"),
        ("zh", "命令"),
        ("ko", "명령"),
        ("th", "คำสั่ง"),
        ("ar", "الأوامر"),
        ("he", "פקודות"),
    ),
    "supported_languages": (
        ("en", "Supported languages"),
        ("ja", "対応言語"),
        ("zh", "支持的语言"),
        ("ko", "지원 언어"),
        ("th", "ภาษาที่รองรับ"),
        ("ar", "اللغات المدعومة"),
        ("he", "שפות נתמכות"),
    ),
    "plans": (
        ("en", "Plans"),
        ("ja", "プラン"),
        ("zh", "方案"),
        ("ko", "요금제"),
        ("th", "แพ็กเกจ"),
        ("ar", "الخطط"),
        ("he", "מסלולים"),
    ),
    "billing": (
        ("en", "Billing"),
        ("ja", "お支払い"),
        ("zh", "账单"),
        ("ko", "결제"),
        ("th", "การเรียกเก็บเงิน"),
        ("ar", "الفوترة"),
        ("he", "חיוב"),
    ),
    "view_plans": (
        ("en", "View plans"),
        ("ja", "プランを見る"),
        ("zh", "查看方案"),
        ("ko", "요금제 보기"),
        ("th", "ดูแพ็กเกจ"),
        ("ar", "عرض الخطط"),
        ("he", "הצגת המסלולים"),
    ),
    "manage_billing": (
        ("en", "Manage billing"),
        ("ja", "支払いを管理"),
        ("zh", "管理账单"),
        ("ko", "결제 관리"),
        ("th", "จัดการบิล"),
        ("ar", "إدارة الفوترة"),
        ("he", "ניהול חיוב"),
    ),
    "contact": (
        ("en", "Contact us"),
        ("ja", "お問い合わせ"),
        ("zh", "联系我们"),
        ("ko", "문의하기"),
        ("th", "ติดต่อเรา"),
        ("ar", "اتصل بنا"),
        ("he", "יצירת קשר"),
    ),
    "open_link": (
        ("en", "Open link"),
        ("ja", "リンクを開く"),
        ("zh", "打开链接"),
        ("ko", "링크 열기"),
        ("th", "เปิดลิงก์"),
        ("ar", "فتح الرابط"),
        ("he", "פתיחת הקישור"),
    ),
    "qr_howto": (
        ("en", "How to use"),
        ("ja", "使い方"),
        ("zh", "使用方法"),
        ("ko", "사용법"),
        ("th", "วิธีใช้"),
        ("ar", "طريقة الاستخدام"),
        ("he", "איך להשתמש"),
    ),
    "qr_languages": (
        ("en", "Change languages"),
        ("ja", "言語設定"),
        ("zh", "更改语言"),
        ("ko", "언어 설정"),
        ("th", "ตั้งค่าภาษา"),
        ("ar", "تغيير اللغة"),
        ("he", "שינוי שפות"),
    ),
    "qr_stop": (
        ("en", "Stop translation"),
        ("ja", "翻訳停止"),
        ("zh", "停止翻译"),
        ("ko", "번역 중지"),
        ("th", "หยุดแปล"),
        ("ar", "إيقاف الترجمة"),
        ("he", "עצירת תרגום"),
    ),
    "qr_plan": (
        ("en", "Subscription"),
        ("ja", "プラン"),
        ("zh", "订阅"),
        ("ko", "구독"),
        ("th", "แพ็กเกจ"),
        ("ar", "الاشتراك"),
        ("he", "מנוי"),
    ),
    "cmd_howto": (
        ("en", "How to use"),
        ("ja", "使い方"),
        ("zh", "使用方法"),
        ("ko", "사용법"),
        ("th", "วิธีใช้"),
        ("ar", "طريقة الاستخدام"),
        ("he", "איך להשתמש"),
    ),
    "cmd_languages": (
        ("en", "Change language settings"),
        ("ja", "言語設定の変更"),
        ("zh", "更改语言设置"),
        ("ko", "언어 설정 변경"),
        ("th", "เปลี่ยนการตั้งค่าภาษา"),
        ("ar", "تغيير إعدادات اللغة"),
        ("he", "שינוי הגדרות השפה"),
    ),
    "cmd_stop": (
        ("en", "Stop translation"),
        ("ja", "翻訳を停止して"),
        ("zh", "停止翻译"),
        ("ko", "번역 중지"),
        ("th", "หยุดการแปล"),
        ("ar", "إيقاف الترجمة"),
        ("he", "עצירת תרגום"),
    ),
    "cmd_plan": (
        ("en", "Subscription management"),
        ("ja", "サブスクリプション"),
        ("zh", "订阅管理"),
        ("ko", "구독 관리"),
        ("th", "จัดการแพ็กเกจ"),
        ("ar", "إدارة الاشتراك"),
        ("he", "ניהול מנוי"),
    ),
}


JOIN_GUIDANCE = (
    "I'm a multilingual translation bot. Please tell me the languages you want to translate to.\n\n"
    "多言語翻訳ボットです。翻訳したい言語を教えてください。\n\n"
    "我是一个多语言翻译机器人。请告诉我你想要翻译成哪些语言。\n\n"
    "ฉันเป็นบอทแปลหลายภาษา กรุณาบอกฉันว่าคุณต้องการแปลเป็นภาษาใดบ้าง\n\n"
    "ex) English, 中文, 日本語, ไทย"
)

_MEMBER_JOINED_BODY = (
    "When you want to change the interpreter's language settings, please remove this bot from the group once and then invite it again!\n\n"
    "通訳の言語設定を変更するときは、このボットを一度グループから削除してから、再度招待してね！\n\n"
    "如果你想更改口译语言设置，请先将此机器人从群组中删除，然后再重新邀请它！\n\n"
    "หากคุณต้องการเปลี่ยนการตั้งค่าภาษาของล่าม กรุณานำบอทนี้ออกจากกลุ่มก่อน แล้วค่อยเชิญกลับมาอีกครั้ง!"
)


def localized_label(key: str, lang: str) -> str:
    """固定ラベル。未知の言語は英語の基準文を返す。"""

    options = _LABELS.get(key) or ()
    lowered = (lang or "").strip().lower().replace("_", "-")
    if lowered:
        for prefix, text in options:
            if prefix != "en" and lowered.startswith(prefix):
                return text
    for prefix, text in options:
        if prefix == "en":
            return text
    return ""


def command_quick_replies(lang: str, bot_name: str) -> tuple[RichButton, ...]:
    """未知コマンド向け。押すと @ボット名 + コマンド文が送られる。"""

    name = " ".join((bot_name or "").split())
    if not name:
        return ()
    pairs = (
        ("qr_howto", "cmd_howto"),
        ("qr_languages", "cmd_languages"),
        ("qr_stop", "cmd_stop"),
        ("qr_plan", "cmd_plan"),
    )
    replies: list[RichButton] = []
    for label_key, text_key in pairs:
        label = localized_label(label_key, lang)
        command = localized_label(text_key, lang)
        if not label or not command:
            continue
        replies.append(RichButton(label=label, text=f"@{name} {command}"))
    return tuple(replies)


def link_buttons(text: str, *, lang: str) -> tuple[str, tuple[RichButton, ...]]:
    """本文中の URL をボタンへ移す。お問い合わせ URL だけラベルを分ける。"""

    body, urls = split_urls(text)
    buttons: list[RichButton] = []
    for url in urls:
        key = "contact" if "contact" in url.lower() else "open_link"
        buttons.append(RichButton(label=localized_label(key, lang), uri=url, style="primary"))
    return body.strip(), tuple(buttons)


def _card(
    *,
    title_key: str,
    lang: str,
    body: str,
    plain_text: str = "",
    buttons: tuple[RichButton, ...] = (),
    quick_replies: tuple[RichButton, ...] = (),
) -> GuidanceMessage:
    shown = (body or "").strip()
    return GuidanceMessage(
        title=localized_label(title_key, lang),
        body=shown,
        plain_text=(plain_text or shown).strip(),
        buttons=buttons,
        quick_replies=quick_replies,
    )


def join_card() -> GuidanceMessage:
    """グループ参加時の言語ヒアリング。"""

    return _card(title_key="language_settings", lang="en", body=JOIN_GUIDANCE)


def member_joined_card(prefix: str) -> GuidanceMessage:
    """参加メンバーへの言語設定の案内。"""

    name = (prefix or "").strip() or "everyone"
    body = f"Hello {name} !\n\n{_MEMBER_JOINED_BODY}"
    return _card(title_key="language_settings", lang="en", body=body)


def howto_card(text: str, *, lang: str) -> GuidanceMessage:
    """使い方。URL はボタンに隠し、番号付き手順の改行は保つ。"""

    normalized = normalize_inline_lists(text or "").strip()
    body, buttons = link_buttons(normalized, lang=lang)
    return _card(
        title_key="how_to_use",
        lang=lang,
        body=body or normalized,
        plain_text=normalized,
        buttons=buttons,
    )


def settings_help_card(text: str, *, lang: str) -> GuidanceMessage:
    """言語の変え方。例文は本文に残す。"""

    return _card(title_key="language_settings", lang=lang, body=text or "")


def catalog_card(text: str, *, lang: str) -> GuidanceMessage:
    """対応言語の一覧。"""

    return _card(title_key="supported_languages", lang=lang, body=text or "")


def unknown_command_card(text: str, *, lang: str, bot_name: str) -> GuidanceMessage:
    """未知のメンション。コマンドはクイックリプライから送れる。"""

    return _card(
        title_key="commands",
        lang=lang,
        body=text or "",
        quick_replies=command_quick_replies(lang, bot_name),
    )


def language_reset_card(text: str, *, lang: str) -> GuidanceMessage:
    """言語設定を消したあとの再設定案内。"""

    return _card(title_key="language_settings", lang=lang, body=text or "")


def quota_notice_card(text: str, *, lang: str, url: str | None) -> GuidanceMessage:
    """月間上限の案内。購入 URL はプランボタンの裏に置く。"""

    buttons: tuple[RichButton, ...] = ()
    if url:
        buttons = (RichButton(label=localized_label("view_plans", lang), uri=url, style="primary"),)
    plain = (text or "").strip()
    if url and url not in plain:
        plain = f"{plain}\n\n{url}".strip()
    return _card(title_key="plans", lang=lang, body=text or "", plain_text=plain, buttons=buttons)


def billing_notice_card(text: str, *, lang: str, url: str | None) -> GuidanceMessage:
    """請求先が変わった案内。続行用 URL はボタンの裏に置く。"""

    buttons: tuple[RichButton, ...] = ()
    if url:
        buttons = (RichButton(label=localized_label("manage_billing", lang), uri=url, style="primary"),)
    plain = (text or "").strip()
    if url and url not in plain:
        plain = f"{plain}\n\n{url}".strip()
    return _card(title_key="billing", lang=lang, body=text or "", plain_text=plain, buttons=buttons)
