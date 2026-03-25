import importlib
import sys
import types
from datetime import datetime, timezone


def _import_module(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec")
    sys.modules.setdefault("psycopg", types.SimpleNamespace(connect=lambda *args, **kwargs: None))
    sys.modules.setdefault(
        "stripe",
        types.SimpleNamespace(
            http_client=types.SimpleNamespace(RequestsClient=lambda: None),
            default_http_client=None,
        ),
    )
    sys.modules.pop("src.stripe_webhook_handler", None)
    return importlib.import_module("src.stripe_webhook_handler")


def test_sync_subscription_passes_billing_owner_user_id(monkeypatch):
    module = _import_module(monkeypatch)
    captured = {}

    monkeypatch.setattr(
        module,
        "_upsert_subscription",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(module, "price_catalog", type("_Catalog", (), {"resolve_price": staticmethod(lambda _price_id: None)})())

    module._sync_subscription(  # pylint: disable=protected-access
        "gid_1",
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_start": int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()),
            "current_period_end": int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp()),
            "metadata": {"group_id": "gid_1", "line_user_id": "U555"},
            "items": {"data": [{"price": {"id": "price_x"}}]},
        },
        status_override="active",
    )

    assert captured["billing_owner_user_id"] == "U555"
