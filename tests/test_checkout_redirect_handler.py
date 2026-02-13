import importlib
import json
import sys
import types


def _import_handler(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("SUBSCRIPTION_TOKEN_SECRET", "token_secret")
    sys.modules.setdefault(
        "psycopg_pool",
        types.SimpleNamespace(ConnectionPool=object),
    )
    sys.modules.setdefault(
        "psycopg",
        types.SimpleNamespace(
            errors=types.SimpleNamespace(UndefinedTable=Exception, UndefinedColumn=Exception),
            sql=types.SimpleNamespace(SQL=lambda text: text),
        ),
    )
    sys.modules.pop("src.checkout_redirect_handler", None)
    return importlib.import_module("src.checkout_redirect_handler")


def test_start_upgrade_returns_checkout_redirect_for_active_subscription(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Catalog:
        @staticmethod
        def resolve_target(_target):
            return "price_target_pro"

        @staticmethod
        def resolve_price(price_id):
            if price_id == "price_current_standard":
                return types.SimpleNamespace(plan="standard", interval="month", is_grandfathered=False)
            return None

    class _Repo:
        @staticmethod
        def get_subscription_detail(_group_id):
            return ("cus_123", "sub_123", "active")

        @staticmethod
        def get_subscription_plan(_group_id):
            return ("active", "standard", "month", False, None, None, None, None, None, None)

    calls = []

    class _SessionApi:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(url="https://billing.stripe.com/session/test")

    class _SubApi:
        @staticmethod
        def retrieve(_sub_id, expand=None):  # noqa: ARG004
            return {
                "items": {
                    "data": [
                        {
                            "id": "si_123",
                            "price": {"id": "price_current_standard"},
                        }
                    ]
                },
                "current_period_start": 1771000000,
                "current_period_end": 1773600000,
                "status": "active",
            }

    fake_stripe = types.SimpleNamespace(
        api_key="",
        billing_portal=types.SimpleNamespace(Session=_SessionApi),
        Subscription=_SubApi,
    )

    monkeypatch.setattr(module, "verify_token", lambda *_args, **_kwargs: {"group_id": "gid_1"})
    monkeypatch.setattr(module, "build_price_catalog", lambda _settings: _Catalog())
    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())
    monkeypatch.setattr(module, "_import_stripe", lambda: fake_stripe)

    event = {
        "queryStringParameters": {
            "mode": "start",
            "st": "token",
            "target": "pro_monthly",
        }
    }
    response = module.lambda_handler(event, None)
    assert response["statusCode"] == 200

    body = json.loads(response["body"])
    assert body["result"] == "checkout_created"
    assert body["redirectUrl"] == "https://billing.stripe.com/session/test"
    assert calls and calls[0]["flow_data"]["type"] == "subscription_update_confirm"


def test_status_returns_translation_count_for_current_period(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Repo:
        @staticmethod
        def get_subscription_plan(_group_id):
            return (
                "active",
                "standard",
                "month",
                False,
                "price_standard_monthly",
                module._to_datetime(1771000000),
                module._to_datetime(1773600000),
                14,
                None,
                None,
            )

        @staticmethod
        def get_usage(_group_id, period_key):
            assert period_key == "2026-02-13"
            return 123

    monkeypatch.setattr(module, "verify_token", lambda *_args, **_kwargs: {"group_id": "gid_1"})
    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())

    event = {"queryStringParameters": {"mode": "status", "st": "token"}}
    response = module.lambda_handler(event, None)
    assert response["statusCode"] == 200

    body = json.loads(response["body"])
    assert body["effectivePlan"] == "standard"
    assert body["periodKey"] == "2026-02-13"
    assert body["translationCount"] == 123
