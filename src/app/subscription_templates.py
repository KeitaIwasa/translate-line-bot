from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional, Sequence

from .subscription_postback import encode_subscription_payload
from .subscription_texts import (
    SUBS_CANCEL_CONFIRM_TEXT,
    SUBS_CANCEL_LABEL,
    SUBS_MENU_TEXT,
    SUBS_MENU_TITLE,
    SUBS_NO_LABEL,
    SUBS_UPGRADE_LABEL,
    SUBS_VIEW_LABEL,
    SUBS_YES_LABEL,
)
from .ui_strings import ui_text
from ..domain.services.subscription_service import SubscriptionService

TranslateFn = Callable[[str], str]
TruncateFn = Callable[[str, int], str]
NormalizeFn = Callable[[str], str]


def _translate_summary(
    summary: str,
    translate: TranslateFn,
    translate_batch: Optional[Callable[[Sequence[str]], Sequence[str]]],
) -> str:
    """プラン状態だけを翻訳する。固定のタイトルやボタンはここを通さない。"""

    if translate_batch is not None:
        try:
            batched = list(translate_batch([summary]))
        except Exception:
            batched = []
        if len(batched) == 1 and isinstance(batched[0], str) and batched[0].strip():
            return batched[0].strip()
    return (translate(summary) or summary).strip() or summary


def build_subscription_menu_message(
    *,
    group_id: str,
    instruction_lang: str,
    status: Optional[str],
    effective_plan: Optional[str],
    period_end: Optional[datetime],
    portal_url: Optional[str],
    upgrade_url: Optional[str],
    include_upgrade: bool,
    include_cancel: bool,
    translate: TranslateFn,
    truncate: TruncateFn,
    normalize_text: NormalizeFn,
    translate_batch: Optional[Callable[[Sequence[str]], Sequence[str]]] = None,
) -> Optional[Dict]:
    summary = SubscriptionService.build_subscription_summary_text(
        status,
        period_end,
        plan_key=effective_plan,
    )
    translated_summary = _translate_summary(summary, translate, translate_batch)
    title = ui_text(SUBS_MENU_TITLE, instruction_lang)
    alt_text = ui_text(SUBS_MENU_TEXT, instruction_lang)
    body_text = truncate(normalize_text(translated_summary), 120)

    actions = []
    if portal_url:
        label = ui_text(SUBS_VIEW_LABEL, instruction_lang)
        actions.append({"type": "uri", "label": truncate(label, 20), "uri": portal_url})

    if include_cancel:
        label = ui_text(SUBS_CANCEL_LABEL, instruction_lang)
        payload = encode_subscription_payload({
            "kind": "cancel",
            "group_id": group_id,
            "instruction_lang": instruction_lang,
        })
        actions.append({"type": "postback", "label": truncate(label, 20), "data": payload})

    if include_upgrade and upgrade_url:
        label = ui_text(SUBS_UPGRADE_LABEL, instruction_lang)
        actions.append({"type": "uri", "label": truncate(label, 20), "uri": upgrade_url})

    if not actions:
        return None

    return {
        "type": "template",
        "altText": truncate(alt_text, 400),
        "template": {
            "type": "buttons",
            "title": truncate(title, 40),
            "text": body_text,
            "actions": actions,
        },
    }


def build_subscription_cancel_confirm(
    *,
    group_id: str,
    language: str,
    truncate: TruncateFn,
    normalize_text: NormalizeFn,
    base_confirm_text: str = SUBS_CANCEL_CONFIRM_TEXT,
) -> Dict:
    confirm_text = ui_text(base_confirm_text, language)
    yes_label = ui_text(SUBS_YES_LABEL, language)
    no_label = ui_text(SUBS_NO_LABEL, language)

    payload_yes = encode_subscription_payload({"kind": "cancel_confirm", "group_id": group_id})
    payload_no = encode_subscription_payload({"kind": "cancel_reject", "group_id": group_id})

    return {
        "type": "template",
        "altText": truncate(confirm_text, 400),
        "template": {
            "type": "confirm",
            "text": truncate(normalize_text(confirm_text), 240),
            "actions": [
                {"type": "postback", "label": truncate(yes_label, 12), "data": payload_yes},
                {"type": "postback", "label": truncate(no_label, 12), "data": payload_no},
            ],
        },
    }
