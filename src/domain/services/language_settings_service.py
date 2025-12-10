from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .. import models
from ..ports import LanguagePreferencePort, MessageRepositoryPort
from .interface_translation_service import InterfaceTranslationService
from ...presentation.reply_formatter import RTL_LANG_PREFIXES, _wrap_bidi_isolate, strip_source_echo


class LanguageSettingsService:
    """言語設定フロー（解析→確認→保存）を担当するサービス。"""

    def __init__(
        self,
        repo: MessageRepositoryPort,
        preference_analyzer: LanguagePreferencePort,
        interface_translation: InterfaceTranslationService,
        max_group_languages: int,
    ) -> None:
        self._repo = repo
        self._pref = preference_analyzer
        self._interface_translation = interface_translation
        self._max_group_languages = max_group_languages

    def propose(self, event: models.MessageEvent) -> models.ReplyBundle | None:
        try:
            result = self._pref.analyze(event.text)
        except Exception:  # pylint: disable=broad-except
            return models.ReplyBundle(texts=[self._language_analysis_fallback()])

        if not result:
            return models.ReplyBundle(texts=[self._language_analysis_fallback()])

        supported = result.supported
        unsupported = result.unsupported

        detected_total = len(supported) + len(unsupported)
        if detected_total > self._max_group_languages:
            message = self._build_language_limit_message(result.primary_language)
            self._repo.set_translation_enabled(event.group_id, False)
            return models.ReplyBundle(texts=[message[:5000]])

        messages: List[Dict] = []
        limited_supported, dropped = self._limit_language_choices(supported)
        if unsupported:
            messages.append({"type": "text", "text": self._format_unsupported_message(unsupported, result.primary_language)})
        if dropped:
            notice = self._build_language_limit_message(result.primary_language)
            messages.append({"type": "text", "text": notice})
            self._repo.set_translation_enabled(event.group_id, False)
            if messages:
                return models.ReplyBundle(messages=messages)
            return None

        if not limited_supported:
            if messages:
                return models.ReplyBundle(messages=messages)
            return None

        prompt_texts = self._prepare_language_prompt_texts(limited_supported, result)
        confirm_payload = self._encode_postback_payload(
            {
                "kind": "language_confirm",
                "action": "confirm",
                "languages": [{"code": lang.code, "name": lang.name} for lang in limited_supported],
                "primary_language": prompt_texts["primary_language"],
                "completion_text": prompt_texts["completion_text"],
                "limit_text": self._build_language_limit_message(result.primary_language),
            }
        )
        cancel_payload = self._encode_postback_payload(
            {
                "kind": "language_confirm",
                "action": "cancel",
                "primary_language": prompt_texts["primary_language"],
                "cancel_text": prompt_texts["cancel_text"],
            }
        )

        template_message = {
            "type": "template",
            "altText": "Confirm interpretation languages",
            "template": {
                "type": "confirm",
                "text": prompt_texts["confirm_text"],
                "actions": [
                    {"type": "postback", "label": f"🆗 {prompt_texts['confirm_label']}", "data": confirm_payload},
                    {"type": "postback", "label": f"↩️ {prompt_texts['cancel_label']}", "data": cancel_payload},
                ],
            },
        }

        messages.append(template_message)
        self._repo.record_language_prompt(event.group_id)
        self._repo.set_translation_enabled(event.group_id, False)
        return models.ReplyBundle(messages=messages)

    def confirm(
        self,
        *,
        group_id: str,
        languages: Sequence[Tuple[str, str]],
        primary_language: str,
        completion_text: str | None = None,
        limit_text: str | None = None,
    ) -> models.ReplyBundle | None:
        tuples = self._dedup_languages(languages)
        if len(tuples) > self._max_group_languages:
            warning = limit_text or self._build_language_limit_message(primary_language)
            return models.ReplyBundle(texts=[warning])

        completed = self._repo.try_complete_group_languages(group_id, tuples)
        if not completed:
            return None
        self._repo.set_translation_enabled(group_id, True)

        base_text = completion_text or self._build_completion_message(tuples)
        text = self._build_multilingual_completion_message(base_text, tuples)
        return models.ReplyBundle(texts=[text])

    def cancel(self, *, group_id: str, primary_language: str, cancel_text: str | None = None) -> models.ReplyBundle | None:
        cancelled = self._repo.try_cancel_language_prompt(group_id)
        if not cancelled:
            return None
        self._repo.set_translation_enabled(group_id, False)
        base = cancel_text or self._build_cancel_message()
        return models.ReplyBundle(texts=[base])

    # --- helpers ---
    def _limit_language_choices(
        self, languages: Sequence[models.LanguageChoice]
    ) -> Tuple[List[models.LanguageChoice], List[models.LanguageChoice]]:
        limited: List[models.LanguageChoice] = []
        dropped: List[models.LanguageChoice] = []
        seen = set()
        for lang in languages:
            code = (lang.code or "").lower()
            if not code or code in seen:
                continue
            seen.add(code)
            if len(limited) < self._max_group_languages:
                limited.append(models.LanguageChoice(code=code, name=lang.name))
            else:
                dropped.append(models.LanguageChoice(code=code, name=lang.name))
        return limited, dropped

    def _format_unsupported_message(
        self, unsupported: Sequence[models.LanguageChoice], instruction_lang: str
    ) -> str:
        names = [lang.name or lang.code for lang in unsupported if lang.code]
        filtered = [name for name in names if name]
        base = "The following languages are not supported: " + ", ".join(filtered)
        translated = self._translate_template(base, instruction_lang, force=True)
        return translated or base

    def _build_language_limit_message(self, instruction_lang: str) -> str:
        base = f"You can set up to {self._max_group_languages} translation languages. Please specify {self._max_group_languages} or fewer."
        if not instruction_lang or instruction_lang.lower().startswith("en"):
            return base
        translated = self._translate_template(base, instruction_lang, force=True)
        return translated or base

    def _prepare_language_prompt_texts(self, supported, preference: models.LanguagePreference) -> Dict[str, str]:
        primary_lang = (preference.primary_language or "").lower()

        base_confirm = self._build_simple_confirm_text(supported)
        base_cancel = self._build_cancel_message()
        base_confirm_label = preference.confirm_label or "OK"
        base_cancel_label = preference.cancel_label or "Cancel"

        translated = self._translate_template(
            [base_confirm, base_cancel, base_confirm_label, base_cancel_label],
            primary_lang,
            force=True,
        )
        (
            translated_confirm,
            translated_cancel,
            translated_confirm_label,
            translated_cancel_label,
        ) = translated if isinstance(translated, list) else [base_confirm, base_cancel, base_confirm_label, base_cancel_label]

        confirm_text = self._normalize_template_text(translated_confirm or base_confirm)
        confirm_text = self._truncate(confirm_text or base_confirm, 240)

        base_completion = self._build_completion_message([(lang.code, lang.name) for lang in supported])
        completion_text = self._normalize_template_text(base_completion)
        completion_text = self._truncate(completion_text or base_completion, 240)

        cancel_text = self._normalize_template_text(translated_cancel or base_cancel)
        cancel_text = self._truncate(cancel_text or base_cancel, 240)

        return {
            "confirm_text": confirm_text,
            "confirm_label": translated_confirm_label or base_confirm_label,
            "cancel_label": translated_cancel_label or base_cancel_label,
            "completion_text": completion_text,
            "cancel_text": cancel_text,
            "primary_language": primary_lang,
        }

    def _translate_template(
        self,
        base_text: str | Sequence[str],
        instruction_lang: str,
        *,
        force: bool = False,
    ) -> str | List[str]:
        if isinstance(base_text, str):
            originals = [base_text]
            is_sequence = False
        else:
            originals = list(base_text)
            is_sequence = True

        if not instruction_lang:
            return base_text

        lowered = instruction_lang.lower()
        if lowered.startswith("en") and not force:
            return base_text

        if not originals:
            return base_text

        delimiter = "\n---\n"
        joined = delimiter.join(originals)

        if not self._interface_translation or getattr(self._interface_translation, "_translator", None) is None:
            return base_text

        translations = self._interface_translation.translate(joined, [instruction_lang])
        if not translations:
            return base_text

        translated = strip_source_echo(joined, translations[0].text) or translations[0].text or joined
        parts = translated.split(delimiter)
        if len(parts) != len(originals):
            return base_text

        normalized = [self._normalize_template_text(part or orig) for part, orig in zip(parts, originals)]
        if is_sequence:
            return normalized
        return normalized[0]

    def _build_multilingual_completion_message(self, base_text: str, languages: Sequence[Tuple[str, str]]) -> str:
        deduped = [code.lower() for code, _ in languages if code]
        deduped = [lang for idx, lang in enumerate(deduped) if lang and lang not in deduped[:idx]]

        # 英語が設定に含まれる場合のみ英語行を出す
        include_en = any(lang.startswith("en") for lang in deduped)

        target_langs = [lang for lang in deduped if not lang.startswith("en")]
        if not self._interface_translation or not target_langs:
            return base_text if include_en else "\n\n".join([base_text.strip()] if include_en else [])

        translations = []
        try:
            translations = self._interface_translation.translate(base_text, target_langs)
        except Exception:  # pylint: disable=broad-except
            return base_text if include_en else "\n\n".join([base_text.strip()] if include_en else [])

        text_by_lang = {}
        for item in translations or []:
            lowered = (item.lang or "").lower()
            if not lowered or lowered in text_by_lang:
                continue
            cleaned = strip_source_echo(base_text, item.text)
            text_by_lang[lowered] = (cleaned or item.text or base_text).strip()

        def _maybe_wrap_bidi(text: str, lang: str) -> str:
            if (lang or "").lower().startswith(RTL_LANG_PREFIXES):
                return _wrap_bidi_isolate(text, lang)
            return text

        lines: List[str] = []
        seen_texts = set()
        if include_en:
            base_line = base_text.strip()
            if base_line:
                lines.append(base_line)
                seen_texts.add(base_line)

        for lang in target_langs:
            translated = text_by_lang.get(lang)
            if translated and translated not in seen_texts:
                cleaned = translated.strip()
                lines.append(_maybe_wrap_bidi(cleaned, lang))
                seen_texts.add(cleaned)
        return "\n\n".join(lines)

    @staticmethod
    def _encode_postback_payload(payload: Dict, max_bytes: int = 280) -> str:
        import base64
        import json
        import zlib

        def _encode(data: Dict) -> str:
            raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
            compressed = base64.urlsafe_b64encode(zlib.compress(raw)).decode("ascii").rstrip("=")
            return f"langpref2={compressed}"

        encoded = _encode(payload)
        if len(encoded.encode("utf-8")) <= max_bytes:
            return encoded

        def _shrink_text(key: str, factor: float = 0.6) -> bool:
            if key in payload and payload[key]:
                text = payload[key]
                new_len = max(int(len(text) * factor), 32)
                payload[key] = text[:new_len]
                return True
            return False

        optional_keys = ("limit_text", "cancel_text", "completion_text")
        for key in optional_keys:
            for _ in range(3):
                changed = _shrink_text(key)
                encoded = _encode(payload)
                if len(encoded.encode("utf-8")) <= max_bytes:
                    return encoded
                if not changed:
                    break
            if key in payload:
                payload.pop(key, None)
                encoded = _encode(payload)
                if len(encoded.encode("utf-8")) <= max_bytes:
                    return encoded

        encoded = _encode(payload)
        return encoded[:max_bytes]

    @staticmethod
    def _build_simple_confirm_text(limited_supported) -> str:
        names = [lang.name or lang.code for lang in limited_supported if lang.code]
        filtered = [name for name in names if name]
        if not filtered:
            return "Do you want to enable translation?"
        if len(filtered) == 1:
            joined = filtered[0]
        elif len(filtered) == 2:
            joined = " and ".join(filtered)
        else:
            joined = ", ".join(filtered[:-1]) + ", and " + filtered[-1]
        return f"Do you want to enable translation for {joined}?"

    @staticmethod
    def _build_completion_message(languages) -> str:
        names = [name or code for code, name in languages if code]
        filtered = [name for name in names if name]
        if not filtered:
            return "Translation languages have been updated."
        if len(filtered) == 1:
            joined = filtered[0]
            return f"{joined} has been set as the translation language."
        if len(filtered) == 2:
            joined = " and ".join(filtered)
        else:
            joined = ", ".join(filtered[:-1]) + ", and " + filtered[-1]
        return f"{joined} have been set as the translation languages."

    @staticmethod
    def _build_cancel_message() -> str:
        return "Language update has been cancelled. Please tell me all languages again."

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def _normalize_template_text(text: str) -> str:
        return text.replace("\n\n", "\n").strip()

    @staticmethod
    def _dedup_languages(languages: Sequence[Tuple[str, str]]) -> List[Tuple[str, str]]:
        seen = set()
        results: List[Tuple[str, str]] = []
        for code, name in languages:
            lowered = (code or "").lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            results.append((lowered, name))
        return results

    @staticmethod
    def _language_analysis_fallback() -> str:
        return (
            "ごめんなさい、翻訳する言語の確認に失敗しました。数秒おいてから、翻訳したい言語をカンマ区切りで送ってください。\n"
            "Sorry, I couldn't detect your languages. Please resend after a few seconds (e.g., English, 日本語, 中文, ไทย).\n"
            "ขออภัย ไม่สามารถระบุภาษาได้ กรุณาลองส่งมาใหม่อีกครั้ง (ตัวอย่าง: English, 日本語, 中文, ไทย)"
        )
