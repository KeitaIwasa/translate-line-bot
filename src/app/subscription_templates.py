from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional

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


def build_subscription_menu_message(
    *,
    group_id: str,
    instruction_lang: str,
    status: Optional[str],
    period_end: Optional[datetime],
    portal_url: Optional[str],
    upgrade_url: Optional[str],
    include_upgrade: bool,
    translate: TranslateFn,
    truncate: TruncateFn,
    normalize_text: NormalizeFn,
) -> Optional[Dict]:
    summary = SubscriptionService.build_subscription_summary_text(status, period_end)
    translated_summary = translate(summary) or summary
    body_text = truncate(normalize_text(translated_summary), 120)

    title = translate(SUBS_MENU_TITLE) or SUBS_MENU_TITLE
    alt_text = translate(SUBS_MENU_TEXT) or SUBS_MENU_TEXT

    actions = []
    if portal_url:
        label = translate(SUBS_VIEW_LABEL) or SUBS_VIEW_LABEL
        actions.append({"type": "uri", "label": truncate(label, 20), "uri": portal_url})

    if status:
        label = translate(SUBS_CANCEL_LABEL) or SUBS_CANCEL_LABEL
        payload = encode_subscription_payload({"kind": "cancel", "group_id": group_id})
        actions.append({"type": "postback", "label": truncate(label, 20), "data": payload})

    if include_upgrade and upgrade_url:
        label = translate(SUBS_UPGRADE_LABEL) or SUBS_UPGRADE_LABEL
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
