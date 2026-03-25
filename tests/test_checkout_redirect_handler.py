import importlib
import json
import sys
import types
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse


def _import_handler(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("SUBSCRIPTION_TOKEN_SECRET", "token_secret")
    monkeypatch.setenv("CHECKOUT_SESSION_SECRET", "checkout_secret")
    monkeypatch.setenv("SUBSCRIPTION_FRONTEND_BASE_URL", "https://kotori-ai.com")
    monkeypatch.setenv("LINE_LOGIN_CHANNEL_ID", "2001")
    monkeypatch.setenv("LINE_LOGIN_CHANNEL_SECRET", "line_secret")
    monkeypatch.setenv("LINE_LOGIN_REDIRECT_URI", "https://api.example.com/checkout?mode=auth_callback")
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


def _auth(module, repo, *, owner_user_id=None, owner_forbidden=False, line_user_id="U123", group_id="gid_1"):
    return module._CheckoutAuth(  # pylint: disable=protected-access
        repo=repo,
        group_id=group_id,
        line_user_id=line_user_id,
        owner_user_id=owner_user_id,
        owner_forbidden=owner_forbidden,
    )


def test_auth_start_redirects_to_line_login(monkeypatch):
    module = _import_handler(monkeypatch)
    monkeypatch.setattr(module, "_verify_subscription_token", lambda _token: {"group_id": "gid_1"})

    event = {
        "queryStringParameters": {
            "mode": "auth_start",
            "st": "signed-token",
            "return_to": "/en/pro.html",
        }
    }
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 302
    parsed = urlparse(response["headers"]["Location"])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "access.line.me"
    assert query["client_id"] == ["2001"]
    assert query["redirect_uri"] == ["https://api.example.com/checkout?mode=auth_callback"]
    assert query["scope"] == ["profile openid"]
    assert query["state"][0]


def test_auth_start_ignores_api_base_with_warning(monkeypatch):
    module = _import_handler(monkeypatch)
    monkeypatch.setattr(module, "_verify_subscription_token", lambda _token: {"group_id": "gid_1"})
    warned = {}

    def _capture_warning(message, *args, **kwargs):  # noqa: ARG001
        warned["message"] = message

    monkeypatch.setattr(module.logger, "warning", _capture_warning)

    event = {
        "queryStringParameters": {
            "mode": "auth_start",
            "st": "signed-token",
            "api_base": "https://attacker.example",
        }
    }
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 302
    assert warned["message"] == "Deprecated query parameter ignored: api_base"


def test_auth_callback_redirects_back_with_checkout_session(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Repo:
        @staticmethod
        def is_group_member(_group_id, _user_id):
            return True

    monkeypatch.setattr(
        module,
        "verify_token",
        lambda token, **kwargs: (
            {"st": "signed-token", "return_to": "/th/pro.html"}
            if kwargs.get("scope") == module.CHECKOUT_OAUTH_STATE_SCOPE
            else {"group_id": "gid_1"}
        ),
    )
    monkeypatch.setattr(module, "_exchange_line_login_code", lambda _code: "access-token")
    monkeypatch.setattr(module, "_fetch_line_user_id", lambda _token: "U999")
    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())

    event = {"queryStringParameters": {"mode": "auth_callback", "code": "abc", "state": "state-token"}}
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 302
    parsed = urlparse(response["headers"]["Location"])
    query = parse_qs(parsed.query)
    assert parsed.path == "/th/pro.html"
    assert query["st"] == ["signed-token"]
    assert query["cs"][0]


def test_auth_callback_redirects_with_not_member_error(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Repo:
        @staticmethod
        def is_group_member(_group_id, _user_id):
            return False

    monkeypatch.setattr(
        module,
        "verify_token",
        lambda token, **kwargs: (
            {"st": "signed-token", "return_to": "/pro.html"}
            if kwargs.get("scope") == module.CHECKOUT_OAUTH_STATE_SCOPE
            else {"group_id": "gid_1"}
        ),
    )
    monkeypatch.setattr(module, "_exchange_line_login_code", lambda _code: "access-token")
    monkeypatch.setattr(module, "_fetch_line_user_id", lambda _token: "U999")
    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())

    event = {"queryStringParameters": {"mode": "auth_callback", "code": "abc", "state": "state-token"}}
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 302
    parsed = urlparse(response["headers"]["Location"])
    query = parse_qs(parsed.query)
    assert query["error"] == ["not_member"]


def test_auth_callback_returns_401_when_line_token_exchange_http_error(monkeypatch):
    module = _import_handler(monkeypatch)

    monkeypatch.setattr(
        module,
        "verify_token",
        lambda token, **kwargs: (
            {"st": "signed-token", "return_to": "/pro.html"}
            if kwargs.get("scope") == module.CHECKOUT_OAUTH_STATE_SCOPE
            else {"group_id": "gid_1"}
        ),
    )

    def _raise_http_error(_code):
        raise HTTPError(module.LINE_TOKEN_URL, 500, "server error", hdrs=None, fp=None)

    monkeypatch.setattr(module, "_exchange_line_login_code", _raise_http_error)

    event = {"queryStringParameters": {"mode": "auth_callback", "code": "abc", "state": "state-token"}}
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 401
    body = json.loads(response["body"])
    assert body["message"] == "line login failed"


def test_auth_callback_returns_401_when_line_profile_fetch_url_error(monkeypatch):
    module = _import_handler(monkeypatch)

    monkeypatch.setattr(
        module,
        "verify_token",
        lambda token, **kwargs: (
            {"st": "signed-token", "return_to": "/pro.html"}
            if kwargs.get("scope") == module.CHECKOUT_OAUTH_STATE_SCOPE
            else {"group_id": "gid_1"}
        ),
    )
    monkeypatch.setattr(module, "_exchange_line_login_code", lambda _code: "access-token")

    def _raise_url_error(_token):
        raise URLError("temporary network error")

    monkeypatch.setattr(module, "_fetch_line_user_id", _raise_url_error)

    event = {"queryStringParameters": {"mode": "auth_callback", "code": "abc", "state": "state-token"}}
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 401
    body = json.loads(response["body"])
    assert body["message"] == "line login failed"


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
                module._to_datetime(1771000000),  # pylint: disable=protected-access
                module._to_datetime(1773600000),  # pylint: disable=protected-access
                14,
                None,
                None,
            )

        @staticmethod
        def get_usage(_group_id, period_key):
            assert period_key == "2026-02-13"
            return 123

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())
    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, _Repo(), owner_user_id="U123"))

    event = {"queryStringParameters": {"mode": "status", "st": "token", "cs": "session"}}
    response = module.lambda_handler(event, None)
    assert response["statusCode"] == 200

    body = json.loads(response["body"])
    assert body["effectivePlan"] == "standard"
    assert body["periodKey"] == "2026-02-13"
    assert body["translationCount"] == 123
    assert body["isBillingOwner"] is True


def test_start_requires_billing_owner_for_existing_subscription(monkeypatch):
    module = _import_handler(monkeypatch)
    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, object(), owner_user_id="U999", owner_forbidden=True))

    event = {"queryStringParameters": {"mode": "start", "st": "token", "cs": "session", "target": "pro_monthly"}}
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 403
    body = json.loads(response["body"])
    assert body["reason"] == "owner_only"


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
        owner_updates = []

        @staticmethod
        def get_subscription_detail(_group_id):
            return ("cus_123", "sub_123", "active")

        @staticmethod
        def get_subscription_plan(_group_id):
            return ("active", "standard", "month", False, None, None, None, None, None, None)

        @classmethod
        def set_billing_owner_user_id(cls, group_id, user_id):
            cls.owner_updates.append((group_id, user_id))

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

    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, _Repo(), line_user_id="U123"))
    monkeypatch.setattr(module, "build_price_catalog", lambda _settings: _Catalog())
    monkeypatch.setattr(module, "_import_stripe", lambda: fake_stripe)

    event = {
        "queryStringParameters": {
            "mode": "start",
            "st": "token",
            "cs": "session",
            "target": "pro_monthly",
        }
    }
    response = module.lambda_handler(event, None)
    assert response["statusCode"] == 200

    body = json.loads(response["body"])
    assert body["result"] == "checkout_created"
    assert body["redirectUrl"] == "https://billing.stripe.com/session/test"
    assert calls and calls[0]["flow_data"]["type"] == "subscription_update_confirm"
    assert _Repo.owner_updates == [("gid_1", "U123")]


def test_start_checkout_includes_line_user_id_for_new_subscription(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Catalog:
        @staticmethod
        def resolve_target(_target):
            return "price_target_standard"

    class _Repo:
        @staticmethod
        def get_subscription_detail(_group_id):
            return (None, None, None)

    calls = []

    class _CheckoutSessionApi:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(url="https://checkout.stripe.com/pay/test")

    fake_stripe = types.SimpleNamespace(
        api_key="",
        checkout=types.SimpleNamespace(Session=_CheckoutSessionApi),
    )

    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, _Repo(), line_user_id="U777"))
    monkeypatch.setattr(module, "build_price_catalog", lambda _settings: _Catalog())
    monkeypatch.setattr(module, "_import_stripe", lambda: fake_stripe)

    event = {
        "queryStringParameters": {
            "mode": "start",
            "st": "token",
            "cs": "session",
            "target": "standard_monthly",
        }
    }
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["redirectUrl"] == "https://checkout.stripe.com/pay/test"
    assert calls[0]["metadata"]["line_user_id"] == "U777"
    assert calls[0]["subscription_data"]["metadata"]["line_user_id"] == "U777"
    assert calls[0]["client_reference_id"] == "U777"


def test_portal_returns_billing_portal_url(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Repo:
        @staticmethod
        def get_subscription_detail(_group_id):
            return ("cus_123", "sub_123", "active")

    calls = []

    class _SessionApi:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(url="https://billing.stripe.com/p/session/test")

    fake_stripe = types.SimpleNamespace(
        api_key="",
        billing_portal=types.SimpleNamespace(Session=_SessionApi),
    )

    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, _Repo(), owner_user_id="U123"))
    monkeypatch.setattr(module, "_import_stripe", lambda: fake_stripe)

    event = {"queryStringParameters": {"mode": "portal", "st": "token", "cs": "session"}}
    response = module.lambda_handler(event, None)
    assert response["statusCode"] == 200

    body = json.loads(response["body"])
    assert body["result"] == "portal_created"
    assert body["redirectUrl"] == "https://billing.stripe.com/p/session/test"
    assert calls and calls[0]["customer"] == "cus_123"


def test_portal_returns_401_when_checkout_session_is_missing(monkeypatch):
    module = _import_handler(monkeypatch)
    monkeypatch.setattr(module, "_verify_subscription_token", lambda _token: {"group_id": "gid_1"})

    event = {"queryStringParameters": {"mode": "portal", "st": "token"}}
    response = module.lambda_handler(event, None)
    assert response["statusCode"] == 401


def test_prepare_upgrade_returns_checkout_redirect_without_owner_claim(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Catalog:
        @staticmethod
        def resolve_target(_target):
            return "price_target_pro"

    class _Repo:
        owner_updates = []

        @staticmethod
        def get_subscription_detail(_group_id):
            return ("cus_123", "sub_123", "active")

        @classmethod
        def set_billing_owner_user_id(cls, group_id, user_id):  # pragma: no cover - should not be called
            cls.owner_updates.append((group_id, user_id))

    calls = []

    class _SessionApi:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(url="https://billing.stripe.com/session/prepare-upgrade")

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
                "status": "active",
            }

    fake_stripe = types.SimpleNamespace(
        api_key="",
        billing_portal=types.SimpleNamespace(Session=_SessionApi),
        Subscription=_SubApi,
    )

    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, _Repo(), line_user_id="U123"))
    monkeypatch.setattr(module, "build_price_catalog", lambda _settings: _Catalog())
    monkeypatch.setattr(module, "_import_stripe", lambda: fake_stripe)

    event = {
        "queryStringParameters": {
            "mode": "prepare",
            "st": "token",
            "cs": "session",
            "target": "pro_monthly",
        }
    }
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["result"] == "checkout_created"
    assert body["redirectUrl"] == "https://billing.stripe.com/session/prepare-upgrade"
    assert calls and calls[0]["flow_data"]["type"] == "subscription_update_confirm"
    assert _Repo.owner_updates == []


def test_prepare_checkout_for_new_subscription(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Catalog:
        @staticmethod
        def resolve_target(_target):
            return "price_target_standard"

    class _Repo:
        @staticmethod
        def get_subscription_detail(_group_id):
            return (None, None, None)

    calls = []

    class _CheckoutSessionApi:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(url="https://checkout.stripe.com/pay/prepare-new")

    fake_stripe = types.SimpleNamespace(
        api_key="",
        checkout=types.SimpleNamespace(Session=_CheckoutSessionApi),
    )

    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, _Repo(), line_user_id="U777"))
    monkeypatch.setattr(module, "build_price_catalog", lambda _settings: _Catalog())
    monkeypatch.setattr(module, "_import_stripe", lambda: fake_stripe)

    event = {
        "queryStringParameters": {
            "mode": "prepare",
            "st": "token",
            "cs": "session",
            "target": "standard_monthly",
        }
    }
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["result"] == "checkout_created"
    assert body["redirectUrl"] == "https://checkout.stripe.com/pay/prepare-new"
    assert calls[0]["client_reference_id"] == "U777"


def test_start_downgrade_returns_checkout_redirect_without_schedule_call(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Catalog:
        @staticmethod
        def resolve_target(_target):
            return "price_target_standard"

        @staticmethod
        def resolve_price(price_id):
            if price_id == "price_current_pro":
                return types.SimpleNamespace(plan="pro", interval="month", is_grandfathered=False)
            return None

    class _Repo:
        owner_updates = []

        @staticmethod
        def get_subscription_detail(_group_id):
            return ("cus_123", "sub_123", "active")

        @classmethod
        def set_billing_owner_user_id(cls, group_id, user_id):
            cls.owner_updates.append((group_id, user_id))

    portal_calls = []
    schedule_calls = []

    class _SessionApi:
        @staticmethod
        def create(**kwargs):
            portal_calls.append(kwargs)
            return types.SimpleNamespace(url="https://billing.stripe.com/session/start-downgrade")

    class _SubApi:
        @staticmethod
        def retrieve(_sub_id, expand=None):  # noqa: ARG004
            return {
                "items": {
                    "data": [
                        {
                            "id": "si_123",
                            "price": {"id": "price_current_pro"},
                        }
                    ]
                },
                "status": "active",
            }

    class _ScheduleApi:
        @staticmethod
        def create(**kwargs):  # pragma: no cover - should not be called
            schedule_calls.append(("create", kwargs))
            return {"id": "sub_sched_123"}

        @staticmethod
        def modify(*args, **kwargs):  # pragma: no cover - should not be called
            schedule_calls.append(("modify", args, kwargs))
            return {}

    fake_stripe = types.SimpleNamespace(
        api_key="",
        billing_portal=types.SimpleNamespace(Session=_SessionApi),
        Subscription=_SubApi,
        SubscriptionSchedule=_ScheduleApi,
    )

    monkeypatch.setattr(module, "_authorize_member", lambda _event: _auth(module, _Repo(), line_user_id="U123"))
    monkeypatch.setattr(module, "build_price_catalog", lambda _settings: _Catalog())
    monkeypatch.setattr(module, "_import_stripe", lambda: fake_stripe)

    event = {
        "queryStringParameters": {
            "mode": "start",
            "st": "token",
            "cs": "session",
            "target": "standard_monthly",
        }
    }
    response = module.lambda_handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["result"] == "checkout_created"
    assert body["redirectUrl"] == "https://billing.stripe.com/session/start-downgrade"
    assert portal_calls and portal_calls[0]["flow_data"]["type"] == "subscription_update_confirm"
    assert schedule_calls == []
    assert _Repo.owner_updates == [("gid_1", "U123")]
