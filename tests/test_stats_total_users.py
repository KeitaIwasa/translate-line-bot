import importlib
import json
import sys

from src.infra.neon_repositories import BOT_JOIN_MARKER, GROUP_LANG_MARKER, NeonMessageRepository


class _DummyCursor:
    def __init__(self, row=(0,)):
        self.row = row
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row


class _DummyClient:
    def __init__(self, row=(0,)):
        self.cursor_obj = _DummyCursor(row=row)

    def cursor(self):
        return self.cursor_obj


def _import_handler(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "x")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://example")
    sys.modules.pop("src.stats_total_users_handler", None)
    return importlib.import_module("src.stats_total_users_handler")


def test_get_total_distinct_users_excludes_marker_users():
    client = _DummyClient(row=(1234,))
    repo = NeonMessageRepository(client)

    total = repo.get_total_distinct_users()

    assert total == 1234
    assert client.cursor_obj.params == (BOT_JOIN_MARKER, GROUP_LANG_MARKER)
    assert "COUNT(DISTINCT user_id)" in str(client.cursor_obj.query)


def test_stats_total_users_handler_success(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Repo:
        def get_total_distinct_users(self):
            return 9876

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())

    response = module.lambda_handler({}, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    assert response["headers"]["Cache-Control"] == "public, max-age=300, stale-while-revalidate=60"
    assert body["totalUsers"] == 9876
    assert body["metric"] == "distinct_user_ids"
    assert body["updatedAt"].endswith("Z")


def test_stats_total_users_handler_failure(monkeypatch):
    module = _import_handler(monkeypatch)

    class _Repo:
        def get_total_distinct_users(self):
            raise RuntimeError("db error")

    monkeypatch.setattr(module, "_get_repo", lambda: _Repo())

    response = module.lambda_handler({}, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 500
    assert response["headers"]["Content-Type"] == "application/json"
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    assert response["headers"]["Cache-Control"] == "public, max-age=300, stale-while-revalidate=60"
    assert body == {"message": "Internal Server Error"}
