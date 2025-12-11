from __future__ import annotations

from ...domain import models
from ...domain.ports import LinePort, TranslationPort
from ...domain.services.interface_translation_service import InterfaceTranslationService

# 言語別の定型文（名前あり/なし）
BASE_GREETING_EN = (
    '{name}, nice to meet you! I\'m "KOTORI," an AI translation bot.\n'
    "If you invite me to a group with multilingual users, I will interpret for you!"
)
BASE_GREETING_EN_NONAME = (
    'Nice to meet you! I\'m "KOTORI," an AI translation bot.\n'
    "If you invite me to a group with multilingual users, I will interpret for you!"
)

BASE_GREETING_JA = (
    '{name}さん、はじめまして！私はAI翻訳ボット「KOTORI」です。\n'
    "多言語メンバーのいるグループに招待してくれれば、通訳します！"
)
BASE_GREETING_JA_NONAME = (
    "はじめまして！私はAI翻訳ボット「KOTORI」です。\n"
    "多言語メンバーのいるグループに招待してくれれば、通訳します！"
)

BASE_GREETING_TH = (
    'สวัสดี {name}! ฉันชื่อ "KOTORI" เป็นบอทแปลภาษา AI\n'
    "ถ้าชวนฉันไปยังกลุ่มที่มีหลายภาษา ฉันจะช่วยแปลให้!"
)
BASE_GREETING_TH_NONAME = (
    'สวัสดี! ฉันชื่อ "KOTORI" เป็นบอทแปลภาษา AI\n'
    "ถ้าชวนฉันไปยังกลุ่มที่มีหลายภาษา ฉันจะช่วยแปลให้!"
)

BASE_GREETING_ZH = (
    '{name}，你好！我是 AI 翻譯機器人「KOTORI」。\n'
    "如果你把我邀請到有多語使用者的群組，我會幫忙口譯！"
)
BASE_GREETING_ZH_NONAME = (
    "你好！我是 AI 翻譯機器人「KOTORI」。\n"
    "如果你把我邀請到有多語使用者的群組，我會幫忙口譯！"
)

FALLBACK_ORDER = ["ja", "th", "zh-TW", "en"]
SUPPORTED_BASE = {"en": BASE_GREETING_EN, "ja": BASE_GREETING_JA, "th": BASE_GREETING_TH, "zh": BASE_GREETING_ZH}
SUPPORTED_BASE_NONAME = {
    "en": BASE_GREETING_EN_NONAME,
    "ja": BASE_GREETING_JA_NONAME,
    "th": BASE_GREETING_TH_NONAME,
    "zh": BASE_GREETING_ZH_NONAME,
}


class FollowHandler:
    def __init__(self, line_client: LinePort, translator: TranslationPort) -> None:
        self._line = line_client
        self._interface_translation = InterfaceTranslationService(translator)

    def handle(self, event: models.FollowEvent) -> None:
        if not event.reply_token:
            return

        display_name, language = self._safe_get_profile(event)
        base_lang = _normalize_lang(language)

        if base_lang in SUPPORTED_BASE:
            message = _format_base_message(base_lang, display_name)
            self._line.reply_text(event.reply_token, message)
            return

        if base_lang:
            translated = self._translate_via_gemini(display_name, base_lang)
            if translated:
                self._line.reply_text(event.reply_token, translated)
                return

        fallback_messages = _build_fallback_bundle(display_name)
        self._line.reply_messages(event.reply_token, fallback_messages)

    def _translate_via_gemini(self, display_name: str | None, target_lang: str) -> str | None:
        try:
            results = self._interface_translation.translate(_format_base_message("en", display_name), [target_lang])
        except Exception:  # pylint: disable=broad-except
            return None
        if not results:
            return None
        return results[0].text

    def _safe_get_profile(self, event: models.FollowEvent) -> tuple[str | None, str | None]:
        try:
            container_id = event.group_id if event.sender_type in ("group", "room") else None
            user_id = event.user_id or event.group_id or ""
            return self._line.get_profile(event.sender_type, container_id, user_id)
        except Exception:  # pylint: disable=broad-except
            return None, None


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return ""
    lowered = lang.lower()
    return lowered.split("-")[0]


def _format_base_message(lang: str, display_name: str | None) -> str:
    template = SUPPORTED_BASE.get(lang, BASE_GREETING_EN)
    template_noname = SUPPORTED_BASE_NONAME.get(lang, BASE_GREETING_EN_NONAME)
    if display_name:
        return template.format(name=display_name)
    return template_noname


def _build_fallback_bundle(display_name: str | None) -> list[dict]:
    messages = []
    for lang in FALLBACK_ORDER:
        text = _format_base_message(_normalize_lang(lang), display_name)
        messages.append({"type": "text", "text": text})
    return messages
