"""実行時に翻訳しない固定UI文言。

カードのタイトルやボタンなど、毎回同じである文字列は言語コードで引く。
案内サイトと同じ ja / en / zh-TW / th に加え、簡体字の zh-CN も用意する。
未対応の言語は英語に戻す。
"""

from __future__ import annotations

from typing import Dict, Mapping, Sequence

from .subscription_texts import (
    SUBS_ALREADY_PRO_TEXT,
    SUBS_CANCEL_ABORTED_TEXT,
    SUBS_CANCEL_CONFIRM_TEXT,
    SUBS_CANCEL_DONE_TEXT,
    SUBS_CANCEL_FAIL_TEXT,
    SUBS_CANCEL_LABEL,
    SUBS_MENU_TEXT,
    SUBS_MENU_TITLE,
    SUBS_NO_LABEL,
    SUBS_NOT_PRO_TEXT,
    SUBS_OWNER_ONLY_TEXT,
    SUBS_STATUS_UNAVAILABLE_TEXT,
    SUBS_UPGRADE_LABEL,
    SUBS_UPGRADE_LINK_FAIL,
    SUBS_VIEW_LABEL,
    SUBS_YES_LABEL,
)

# 英語原文をキーにする。en の値は原文と一致させる。
_STRINGS: Dict[str, Mapping[str, str]] = {
    SUBS_MENU_TITLE: {
        "en": SUBS_MENU_TITLE,
        "ja": "プラン",
        "zh-TW": "方案",
        "zh-CN": "方案",
        "th": "แพ็กเกจ",
    },
    SUBS_MENU_TEXT: {
        "en": SUBS_MENU_TEXT,
        "ja": "プランを管理します。下から操作を選んでください。",
        "zh-TW": "管理您的方案。請選擇下方的操作。",
        "zh-CN": "管理您的方案。请选择下方的操作。",
        "th": "จัดการแพ็กเกจของคุณ เลือกการดำเนินการด้านล่าง",
    },
    SUBS_VIEW_LABEL: {
        "en": SUBS_VIEW_LABEL,
        "ja": "お支払い管理",
        "zh-TW": "管理帳單",
        "zh-CN": "管理账单",
        "th": "จัดการการชำระเงิน",
    },
    SUBS_CANCEL_LABEL: {
        "en": SUBS_CANCEL_LABEL,
        "ja": "解約する",
        "zh-TW": "取消訂閱",
        "zh-CN": "取消订阅",
        "th": "ยกเลิกการสมัคร",
    },
    SUBS_UPGRADE_LABEL: {
        "en": SUBS_UPGRADE_LABEL,
        "ja": "Proにアップグレード",
        "zh-TW": "升級至 Pro",
        "zh-CN": "升级到 Pro",
        "th": "อัปเกรดเป็น Pro",
    },
    SUBS_ALREADY_PRO_TEXT: {
        "en": SUBS_ALREADY_PRO_TEXT,
        "ja": "すでにProプランをご利用中です。",
        "zh-TW": "您目前已是 Pro 方案。",
        "zh-CN": "您已经在使用 Pro 方案。",
        "th": "คุณใช้แพ็กเกจ Pro อยู่แล้ว",
    },
    SUBS_UPGRADE_LINK_FAIL: {
        "en": SUBS_UPGRADE_LINK_FAIL,
        "ja": "アップグレード用のリンクをいま作成できません。しばらくしてからもう一度お試しいただくか、管理者へご連絡ください。",
        "zh-TW": "目前無法產生升級連結。請稍後再試，或聯絡管理員。",
        "zh-CN": "暂时无法生成升级链接。请稍后再试，或联系管理员。",
        "th": "ยังสร้างลิงก์อัปเกรดไม่ได้ในขณะนี้ โปรดลองอีกครั้งภายหลัง หรือติดต่อผู้ดูแล",
    },
    SUBS_CANCEL_CONFIRM_TEXT: {
        "en": SUBS_CANCEL_CONFIRM_TEXT,
        "ja": "解約しますか？現在の期間が終わるまでは、このままご利用いただけます。",
        "zh-TW": "確定要取消訂閱嗎？在目前計費期間結束前仍可繼續使用。",
        "zh-CN": "确定要取消订阅吗？在当前计费周期结束前仍可继续使用。",
        "th": "ต้องการยกเลิกการสมัครหรือไม่ คุณยังใช้ได้จนสิ้นสุดรอบบิลปัจจุบัน",
    },
    SUBS_CANCEL_DONE_TEXT: {
        "en": SUBS_CANCEL_DONE_TEXT,
        "ja": "現在の期間の終了時に解約されるよう予約しました。",
        "zh-TW": "已安排在目前計費期間結束時取消訂閱。",
        "zh-CN": "已安排在当前计费周期结束时取消订阅。",
        "th": "ตั้งเวลาให้ยกเลิกการสมัครเมื่อสิ้นสุดรอบบิลปัจจุบันแล้ว",
    },
    SUBS_CANCEL_FAIL_TEXT: {
        "en": SUBS_CANCEL_FAIL_TEXT,
        "ja": "解約できませんでした。しばらくしてからもう一度お試しください。",
        "zh-TW": "取消訂閱失敗。請稍後再試。",
        "zh-CN": "取消订阅失败。请稍后再试。",
        "th": "ยกเลิกการสมัครไม่สำเร็จ โปรดลองอีกครั้งภายหลัง",
    },
    SUBS_NOT_PRO_TEXT: {
        "en": SUBS_NOT_PRO_TEXT,
        "ja": "Proプランはご利用ではありません。",
        "zh-TW": "您目前不是 Pro 方案。",
        "zh-CN": "您当前不是 Pro 方案。",
        "th": "คุณไม่ได้ใช้แพ็กเกจ Pro",
    },
    SUBS_OWNER_ONLY_TEXT: {
        "en": SUBS_OWNER_ONLY_TEXT,
        "ja": "請求管理を開けるのは、お支払い登録者だけです。",
        "zh-TW": "只有付款負責人可以開啟帳單管理。",
        "zh-CN": "只有付款负责人可以打开账单管理。",
        "th": "เฉพาะผู้รับผิดชอบการชำระเงินเท่านั้นที่เปิดหน้าจัดการการชำระเงินได้",
    },
    SUBS_YES_LABEL: {
        "en": SUBS_YES_LABEL,
        "ja": "はい",
        "zh-TW": "是",
        "zh-CN": "是",
        "th": "ใช่",
    },
    SUBS_NO_LABEL: {
        "en": SUBS_NO_LABEL,
        "ja": "いいえ",
        "zh-TW": "否",
        "zh-CN": "否",
        "th": "ไม่",
    },
    SUBS_CANCEL_ABORTED_TEXT: {
        "en": SUBS_CANCEL_ABORTED_TEXT,
        "ja": "解約を中止しました。",
        "zh-TW": "已中止取消訂閱。",
        "zh-CN": "已中止取消订阅。",
        "th": "ไม่ได้ยกเลิกการสมัคร",
    },
    SUBS_STATUS_UNAVAILABLE_TEXT: {
        "en": SUBS_STATUS_UNAVAILABLE_TEXT,
        "ja": "ただいまプランの状態を表示できません。",
        "zh-TW": "目前無法顯示方案狀態。",
        "zh-CN": "目前无法显示方案状态。",
        "th": "ไม่สามารถแสดงสถานะแพ็กเกจได้ในขณะนี้",
    },
}

UI_LANGUAGE_CODES = ("en", "ja", "zh-TW", "zh-CN", "th")


def normalize_ui_language(lang: str | None) -> str:
    """言語コードを固定文言テーブルのキーへ寄せる。未対応は en。"""

    raw = (lang or "").strip().replace("_", "-")
    if not raw:
        return "en"
    lowered = raw.lower()
    if lowered in {"ja", "jp"} or lowered.startswith("ja-"):
        return "ja"
    if lowered == "en" or lowered.startswith("en-"):
        return "en"
    if lowered == "th" or lowered.startswith("th-"):
        return "th"
    if lowered in {"zh-tw", "zh-hant", "zh-hk", "zh-mo"} or lowered.startswith("zh-hant-"):
        return "zh-TW"
    if lowered in {"zh", "zh-cn", "zh-hans", "zh-sg"} or lowered.startswith("zh-hans-"):
        return "zh-CN"
    return "en"


def has_ui_text(english: str) -> bool:
    return english in _STRINGS


def ui_text(english: str, lang: str | None) -> str:
    """用意した訳を返す。テーブルに無い文と未対応言語は英語原文。"""

    table = _STRINGS.get(english)
    if not table:
        return english
    return table.get(normalize_ui_language(lang)) or table["en"]


def resolve_ui_language(
    instruction_lang: str | None,
    group_languages: Sequence[str] | None = None,
) -> str:
    """操作言語を優先し、空のときはグループの非英語を使う。"""

    chosen = (instruction_lang or "").strip()
    if not chosen:
        for lang in group_languages or []:
            if lang and not str(lang).lower().startswith("en"):
                chosen = str(lang)
                break
    return chosen or "en"


def ui_text_for_instruction(
    english: str,
    instruction_lang: str | None,
    group_languages: Sequence[str] | None = None,
) -> str:
    return ui_text(english, resolve_ui_language(instruction_lang, group_languages))


def ui_text_multilingual(english: str, languages: Sequence[str] | None) -> str:
    """グループの各言語について固定文言を1行ずつ並べる。同じ文は繰り返さない。"""

    chosen = [str(lang) for lang in (languages or []) if lang]
    if not chosen:
        chosen = ["en"]
    lines: list[str] = []
    seen: set[str] = set()
    for lang in chosen:
        text = ui_text(english, lang).strip()
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
    return "\n\n".join(lines)
