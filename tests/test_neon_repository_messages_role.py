from datetime import datetime, timezone

from src.domain import models
from src.infra.neon_repositories import NeonMessageRepository


class _Cursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params):
        self.executed.append((str(query), params))

    def fetchall(self):
        return list(self.rows)


class _Client:
    def __init__(self, rows=None):
        self.cursor_obj = _Cursor(rows=rows)

    def cursor(self):
        return self.cursor_obj


def test_insert_message_persists_message_role():
    repo = NeonMessageRepository(_Client())

    repo.insert_message(
        models.StoredMessage(
            group_id="U123",
            user_id="__assistant__",
            sender_name="KOTORI",
            text="reply",
            timestamp=datetime.now(timezone.utc),
            message_role="assistant",
        )
    )

    query, params = repo._client.cursor_obj.executed[0]
    assert "message_role" in query
    assert params[-1] == "assistant"


def test_fetch_private_conversation_returns_role_and_chronological_order():
    now = datetime.now(timezone.utc)
    rows = [
        ("KOTORI", "later", now, "assistant"),
        ("Alice", "earlier", now, "user"),
    ]
    repo = NeonMessageRepository(_Client(rows=rows))

    history = repo.fetch_private_conversation("U123", 5)

    assert [item.text for item in history] == ["earlier", "later"]
    assert [item.role for item in history] == ["user", "assistant"]
