from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional, Sequence

from .subscription_postback import encode_subscription_payload
from .subscription_texts import (
    SUBS_CANCEL_LABEL,
    SUBS_MENU_TEXT,
    SUBS_MENU_TITLE,
    SUBS_UPGRADE_LABEL,
    SUBS_VIEW_LABEL,
)
from ..domain.services.subscription_service import SubscriptionService

TranslateFn = Callable[[str], str]
TruncateFn = Callable[[str, int], str]
NormalizeFn = Callable[[str], str]


def _resolve_menu_texts(
    ordered: Sequence[str],
    translate: TranslateFn,
    translate_batch: Optional[Callable[[Sequence[str]], Sequence[str]]],
) -> list[str]:
    """一括翻訳を優先し、件数や中身が欠けた項目は原文（または個別翻訳）に戻す。"""

    if translate_batch is not None:
        try:
            batched = list(translate_batch(ordered))
        except Exception:
            batched = []
        if len(batched) == len(ordered):
            return [
                item.strip() if isinstance(item, str) and item.strip() else original
                for item, original in zip(batched, ordered)
            ]
    return [(translate(text) or text) for text in ordered]


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
    # プラン本文・タイトル・通知文・請求ボタン・アップグレードボタン（解約ボタンがあるときだけ追加）
    ordered = [summary, SUBS_MENU_TITLE, SUBS_MENU_TEXT]
    if portal_url:
        ordered.append(SUBS_VIEW_LABEL)
    if include_cancel:
        ordered.append(SUBS_CANCEL_LABEL)
    if include_upgrade and upgrade_url:
        ordered.append(SUBS_UPGRADE_LABEL)
    resolved = _resolve_menu_texts(ordered, translate, translate_batch)

    cursor = 0
    translated_summary = resolved[cursor]
    cursor += 1
    title = resolved[cursor]
    cursor += 1
    alt_text = resolved[cursor]
    cursor += 1
    body_text = truncate(normalize_text(translated_summary), 120)

    actions = []
    if portal_url:
        label = resolved[cursor]
        cursor += 1
        actions.append({"type": "uri", "label": truncate(label, 20), "uri": portal_url})

    if include_cancel:
        label = resolved[cursor]
        cursor += 1
        payload = encode_subscription_payload({
            "kind": "cancel",
            "group_id": group_id,
            "instruction_lang": instruction_lang,
        })
        actions.append({"type": "postback", "label": truncate(label, 20), "data": payload})

    if include_upgrade and upgrade_url:
        label = resolved[cursor]
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
    translate: TranslateFn,
    truncate: TruncateFn,
    normalize_text: NormalizeFn,
    base_confirm_text: str,
) -> Dict:
    confirm_text = translate(base_confirm_text) or base_confirm_text
    yes_label = translate("Yes") or "Yes"
    no_label = translate("No") or "No"

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
