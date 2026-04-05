import importlib
import sys
import types
from datetime import datetime, timedelta, timezone


def _import_module(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec")
    sys.modules.setdefault(
        "psycopg",
        types.SimpleNamespace(
            connect=lambda *args, **kwargs: None,
            errors=types.SimpleNamespace(UndefinedTable=Exception, UndefinedColumn=Exception),
            sql=types.SimpleNamespace(SQL=lambda text: text),
        ),
    )
    sys.modules.setdefault("psycopg_pool", types.SimpleNamespace(ConnectionPool=object))
    sys.modules.setdefault(
        "stripe",
        types.SimpleNamespace(
            http_client=types.SimpleNamespace(RequestsClient=lambda: None),
            default_http_client=None,
        ),
    )
    sys.modules.pop("src.stripe_webhook_handler", None)
    return importlib.import_module("src.stripe_webhook_handler")


def test_sync_subscription_passes_billing_owner_user_id_from_metadata(monkeypatch):
    module = _import_module(monkeypatch)
    captured = {}

    monkeypatch.setattr(module, "_upsert_subscription", lambda **kwargs: captured.update(kwargs))
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
        event_created=int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()),
    )

    assert captured["billing_owner_user_id"] == "U555"


def test_sync_subscription_confirms_pending_owner_when_webhook_matches(monkeypatch):
    module = _import_module(monkeypatch)
    captured = {}
    confirmed = []
    pending_created_at = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    pending_expires_at = pending_created_at + timedelta(minutes=30)

    class _Repo:
        @staticmethod
        def get_billing_owner_claim_state(_group_id):
            return (None, "U777", "sub_123", pending_expires_at, pending_created_at)

        @staticmethod
        def confirm_pending_billing_owner_claim(group_id, subscription_id, confirmed_user_id):
            confirmed.append((group_id, subscription_id, confirmed_user_id))

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())
    monkeypatch.setattr(module, "_upsert_subscription", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(module, "price_catalog", type("_Catalog", (), {"resolve_price": staticmethod(lambda _price_id: None)})())

    module._sync_subscription(  # pylint: disable=protected-access
        "gid_1",
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_start": int(pending_created_at.timestamp()),
            "current_period_end": int((pending_created_at + timedelta(days=30)).timestamp()),
            "metadata": {"group_id": "gid_1"},
            "items": {"data": [{"price": {"id": "price_x"}}]},
        },
        status_override="active",
        event_created=int((pending_created_at + timedelta(minutes=5)).timestamp()),
    )

    assert captured["billing_owner_user_id"] == "U777"
    assert confirmed == [("gid_1", "sub_123", "U777")]


def test_sync_subscription_does_not_confirm_pending_owner_after_expiry(monkeypatch):
    module = _import_module(monkeypatch)
    captured = {}
    confirmed = []
    pending_created_at = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    pending_expires_at = pending_created_at + timedelta(minutes=30)

    class _Repo:
        @staticmethod
        def get_billing_owner_claim_state(_group_id):
            return (None, "U777", "sub_123", pending_expires_at, pending_created_at)

        @staticmethod
        def confirm_pending_billing_owner_claim(group_id, subscription_id, confirmed_user_id):
            confirmed.append((group_id, subscription_id, confirmed_user_id))

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())
    monkeypatch.setattr(module, "_upsert_subscription", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(module, "price_catalog", type("_Catalog", (), {"resolve_price": staticmethod(lambda _price_id: None)})())

    module._sync_subscription(  # pylint: disable=protected-access
        "gid_1",
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_start": int(pending_created_at.timestamp()),
            "current_period_end": int((pending_created_at + timedelta(days=30)).timestamp()),
            "metadata": {"group_id": "gid_1"},
            "items": {"data": [{"price": {"id": "price_x"}}]},
        },
        status_override="active",
        event_created=int((pending_expires_at + timedelta(seconds=1)).timestamp()),
    )

    assert captured["billing_owner_user_id"] is None
    assert confirmed == []


def test_sync_subscription_does_not_confirm_pending_owner_on_subscription_mismatch(monkeypatch):
    module = _import_module(monkeypatch)
    captured = {}
    confirmed = []
    pending_created_at = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)

    class _Repo:
        @staticmethod
        def get_billing_owner_claim_state(_group_id):
            return (None, "U777", "sub_other", pending_created_at + timedelta(minutes=30), pending_created_at)

        @staticmethod
        def confirm_pending_billing_owner_claim(group_id, subscription_id, confirmed_user_id):
            confirmed.append((group_id, subscription_id, confirmed_user_id))

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())
    monkeypatch.setattr(module, "_upsert_subscription", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(module, "price_catalog", type("_Catalog", (), {"resolve_price": staticmethod(lambda _price_id: None)})())

    module._sync_subscription(  # pylint: disable=protected-access
        "gid_1",
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_start": int(pending_created_at.timestamp()),
            "current_period_end": int((pending_created_at + timedelta(days=30)).timestamp()),
            "metadata": {"group_id": "gid_1"},
            "items": {"data": [{"price": {"id": "price_x"}}]},
        },
        status_override="active",
        event_created=int((pending_created_at + timedelta(minutes=5)).timestamp()),
    )

    assert captured["billing_owner_user_id"] is None
    assert confirmed == []


def test_sync_subscription_does_not_confirm_pending_owner_when_event_is_older_than_claim(monkeypatch):
    module = _import_module(monkeypatch)
    captured = {}
    confirmed = []
    pending_created_at = datetime(2026, 3, 1, 0, 5, tzinfo=timezone.utc)

    class _Repo:
        @staticmethod
        def get_billing_owner_claim_state(_group_id):
            return (None, "U777", "sub_123", pending_created_at + timedelta(minutes=30), pending_created_at)

        @staticmethod
        def confirm_pending_billing_owner_claim(group_id, subscription_id, confirmed_user_id):
            confirmed.append((group_id, subscription_id, confirmed_user_id))

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())
    monkeypatch.setattr(module, "_upsert_subscription", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(module, "price_catalog", type("_Catalog", (), {"resolve_price": staticmethod(lambda _price_id: None)})())

    module._sync_subscription(  # pylint: disable=protected-access
        "gid_1",
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_start": int((pending_created_at - timedelta(days=1)).timestamp()),
            "current_period_end": int((pending_created_at + timedelta(days=29)).timestamp()),
            "metadata": {"group_id": "gid_1"},
            "items": {"data": [{"price": {"id": "price_x"}}]},
        },
        status_override="active",
        event_created=int((pending_created_at - timedelta(seconds=1)).timestamp()),
    )

    assert captured["billing_owner_user_id"] is None
    assert confirmed == []


def test_sync_subscription_does_not_overwrite_existing_owner_with_pending_claim(monkeypatch):
    module = _import_module(monkeypatch)
    captured = {}
    confirmed = []
    pending_created_at = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)

    class _Repo:
        @staticmethod
        def get_billing_owner_claim_state(_group_id):
            return ("U111", "U777", "sub_123", pending_created_at + timedelta(minutes=30), pending_created_at)

        @staticmethod
        def confirm_pending_billing_owner_claim(group_id, subscription_id, confirmed_user_id):
            confirmed.append((group_id, subscription_id, confirmed_user_id))

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())
    monkeypatch.setattr(module, "_upsert_subscription", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(module, "price_catalog", type("_Catalog", (), {"resolve_price": staticmethod(lambda _price_id: None)})())

    module._sync_subscription(  # pylint: disable=protected-access
        "gid_1",
        {
            "id": "sub_123",
            "customer": "cus_123",
            "status": "active",
            "current_period_start": int(pending_created_at.timestamp()),
            "current_period_end": int((pending_created_at + timedelta(days=30)).timestamp()),
            "metadata": {"group_id": "gid_1"},
            "items": {"data": [{"price": {"id": "price_x"}}]},
        },
        status_override="active",
        event_created=int((pending_created_at + timedelta(minutes=5)).timestamp()),
    )

    assert captured["billing_owner_user_id"] is None
    assert confirmed == []
