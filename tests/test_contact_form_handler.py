import importlib
import json
import sys
import types


def _import_handler(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    monkeypatch.setenv("CONTACT_IP_HASH_SALT", "salt")
    sys.modules.pop("src.contact_form_handler", None)
    return importlib.import_module("src.contact_form_handler")


def _event(method="POST", body=None, origin="https://kotori-ai.com"):
    payload = json.dumps(body) if body is not None else ""
    return {
        "requestContext": {"http": {"method": method, "sourceIp": "203.0.113.20"}},
        "headers": {"origin": origin, "user-agent": "pytest"},
        "body": payload,
        "isBase64Encoded": False,
    }


def test_contact_handler_success(monkeypatch):
    module = _import_handler(monkeypatch)

    sent = {}

    class _SesClient:
        def send_email(self, **kwargs):
            sent.update(kwargs)

    monkeypatch.setattr(module, "_get_ses_client", lambda: _SesClient())
    monkeypatch.setattr(module, "_enforce_rate_limit", lambda event: None)

    response = module.lambda_handler(
        _event(body={"email": "user@example.com", "message": "hello from user message"}),
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"ok": True}
    assert sent["FromEmailAddress"] == "no-reply@iwasadigital.com"
    assert sent["Destination"]["ToAddresses"] == ["contact@iwasadigital.com"]
    assert sent["ReplyToAddresses"] == ["user@example.com"]


def test_contact_handler_bad_request(monkeypatch):
    module = _import_handler(monkeypatch)
    response = module.lambda_handler(_event(body={"email": "invalid", "message": "x"}), None)
    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"message": "Invalid request"}


def test_contact_handler_honeypot_returns_ok(monkeypatch):
    module = _import_handler(monkeypatch)
    monkeypatch.setattr(module, "_get_ses_client", lambda: None)
    response = module.lambda_handler(
        _event(
            body={
                "email": "user@example.com",
                "message": "this message should be ignored",
                "website": "spam-bot",
            }
        ),
        None,
    )
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"ok": True}


def test_contact_handler_rate_limited(monkeypatch):
    module = _import_handler(monkeypatch)
    monkeypatch.setattr(
        module,
        "_enforce_rate_limit",
        lambda event: (_ for _ in ()).throw(module.TooManyRequestsError()),
    )
    response = module.lambda_handler(
        _event(body={"email": "user@example.com", "message": "hello from user message"}),
        None,
    )
    assert response["statusCode"] == 429
    assert json.loads(response["body"]) == {"message": "Too many requests"}


def test_contact_handler_internal_error(monkeypatch):
    module = _import_handler(monkeypatch)
    monkeypatch.setattr(module, "_enforce_rate_limit", lambda event: None)
    monkeypatch.setattr(
        module,
        "_get_ses_client",
        lambda: types.SimpleNamespace(
            send_email=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("ses failed"))
        ),
    )
    response = module.lambda_handler(
        _event(body={"email": "user@example.com", "message": "hello from user message"}),
        None,
    )
    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"message": "Internal Server Error"}


def test_contact_handler_preflight(monkeypatch):
    module = _import_handler(monkeypatch)
    response = module.lambda_handler(_event(method="OPTIONS"), None)
    assert response["statusCode"] == 204
    assert response["headers"]["Access-Control-Allow-Origin"] == "https://kotori-ai.com"
    assert "POST" in response["headers"]["Access-Control-Allow-Methods"]


def test_contact_handler_uses_imported_boto3(monkeypatch):
    module = _import_handler(monkeypatch)
    fake_client = object()
    fake_boto3 = types.SimpleNamespace(client=lambda service, region_name=None: fake_client)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    assert module._get_ses_client() is fake_client
