from datetime import datetime

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


def test_subscription_menu_shows_cancel_for_paid():
    msg = build_subscription_menu_message(
        group_id="G",
        instruction_lang="en",
        status="active",
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
    assert any("Cancel" in (label or "") for label in labels)
