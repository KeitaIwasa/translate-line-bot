from datetime import datetime

from src.app.subscription_texts import (
    SUBS_MENU_TEXT,
    SUBS_MENU_TITLE,
    SUBS_UPGRADE_LABEL,
    SUBS_VIEW_LABEL,
)
from src.app.subscription_templates import build_subscription_menu_message


class _Translator:
    def __call__(self, text):
        return text  # no-op


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _normalize(text: str) -> str:
    return text


def test_subscription_menu_hides_cancel_for_free():
    msg = build_subscription_menu_message(
        group_id="G",
        instruction_lang="en",
        status=None,
        effective_plan="free",
        period_end=None,
        portal_url="https://example.com/portal",
        upgrade_url="https://example.com/upgrade",
        include_upgrade=True,
        include_cancel=False,
        translate=_Translator(),
        truncate=_truncate,
        normalize_text=_normalize,
    )

    assert msg is not None
    labels = [a.get("label") for a in msg["template"]["actions"]]
    assert all("Cancel" not in (label or "") for label in labels)
    assert labels[0] == "Manage billing"


def test_subscription_menu_shows_cancel_for_paid():
    msg = build_subscription_menu_message(
        group_id="G",
        instruction_lang="en",
        status="active",
        effective_plan="pro",
        period_end=datetime(2025, 1, 1),
        portal_url="https://example.com/portal",
        upgrade_url=None,
        include_upgrade=False,
        include_cancel=True,
        translate=_Translator(),
        truncate=_truncate,
        normalize_text=_normalize,
    )

    assert msg is not None
    labels = [a.get("label") for a in msg["template"]["actions"]]
    assert labels[0] == "Manage billing"
    assert any("Cancel" in (label or "") for label in labels)


def test_subscription_menu_translates_five_strings_in_one_batch():
    seen = []

    def translate_batch(texts):
        seen.append(list(texts))
        return [f"JA:{text}" for text in texts]

    msg = build_subscription_menu_message(
        group_id="G",
        instruction_lang="ja",
        status=None,
        effective_plan="free",
        period_end=None,
        portal_url="https://example.com/portal",
        upgrade_url="https://example.com/upgrade",
        include_upgrade=True,
        include_cancel=False,
        translate=lambda text: f"SINGLE:{text}",
        truncate=_truncate,
        normalize_text=_normalize,
        translate_batch=translate_batch,
    )

    assert len(seen) == 1
    assert seen[0][1:] == [SUBS_MENU_TITLE, SUBS_MENU_TEXT, SUBS_VIEW_LABEL, SUBS_UPGRADE_LABEL]
    assert seen[0][0].startswith("Plan: Free")
    assert len(seen[0]) == 5
    assert msg["template"]["title"] == f"JA:{SUBS_MENU_TITLE}"
    assert msg["altText"] == f"JA:{SUBS_MENU_TEXT}"
    labels = [action["label"] for action in msg["template"]["actions"]]
    assert labels == [f"JA:{SUBS_VIEW_LABEL}", f"JA:{SUBS_UPGRADE_LABEL}"]
    assert msg["template"]["text"].startswith("JA:Plan: Free")


def test_subscription_menu_batch_failure_falls_back_to_per_string_translate():
    def translate_batch(_texts):
        return ["only-one"]

    msg = build_subscription_menu_message(
        group_id="G",
        instruction_lang="ja",
        status=None,
        effective_plan="free",
        period_end=None,
        portal_url="https://example.com/portal",
        upgrade_url="https://example.com/upgrade",
        include_upgrade=True,
        include_cancel=False,
        translate=lambda text: f"FB:{text}",
        truncate=_truncate,
        normalize_text=_normalize,
        translate_batch=translate_batch,
    )

    assert msg["template"]["title"] == f"FB:{SUBS_MENU_TITLE}"
    assert msg["template"]["actions"][1]["label"] == f"FB:{SUBS_UPGRADE_LABEL}"
