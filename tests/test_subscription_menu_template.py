from datetime import datetime

from src.app.subscription_templates import build_subscription_cancel_confirm, build_subscription_menu_message
from src.app.ui_strings import ui_text


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


def test_subscription_menu_translates_only_the_dynamic_summary():
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
    assert len(seen[0]) == 1
    assert seen[0][0].startswith("Plan: Free")
    assert msg["template"]["title"] == ui_text("Subscription", "ja")
    assert msg["altText"] == ui_text("Manage your plan. Choose an action below.", "ja")
    labels = [action["label"] for action in msg["template"]["actions"]]
    assert labels == [ui_text("Manage billing", "ja"), ui_text("Upgrade to Pro", "ja")]
    assert msg["template"]["text"].startswith("JA:Plan: Free")
    assert "サブスク" not in msg["template"]["title"]


def test_subscription_menu_keeps_fixed_labels_when_summary_translation_fails():
    def translate_batch(_texts):
        raise RuntimeError("batch failed")

    msg = build_subscription_menu_message(
        group_id="G",
        instruction_lang="ja-JP",
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

    assert msg["template"]["title"] == "プラン"
    assert msg["template"]["text"].startswith("FB:Plan: Free")
    assert msg["template"]["actions"][1]["label"] == "Proにアップグレード"


def test_subscription_cancel_confirm_uses_fixed_labels():
    msg = build_subscription_cancel_confirm(
        group_id="G",
        language="th",
        truncate=_truncate,
        normalize_text=_normalize,
    )

    actions = msg["template"]["actions"]
    assert actions[0]["label"] == "ใช่"
    assert actions[1]["label"] == "ไม่"
    assert msg["template"]["text"] == ui_text(
        "Are you sure you want to cancel your subscription? It will remain active until the end of the current period.",
        "th",
    )
