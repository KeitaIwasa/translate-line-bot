from datetime import datetime, timezone

from src.domain import models
from src.domain.services.private_chat_support_service import PrivateChatSupportConfig, PrivateChatSupportService


class _Repo:
    def __init__(self):
        self.calls = []

    def fetch_private_conversation(self, user_id, limit):
        self.calls.append((user_id, limit))
        return [
            models.ConversationMessage(
                role="user",
                sender_name="Alice",
                text="hello",
                timestamp=datetime.now(timezone.utc),
            )
        ]


class _ResponderPass:
    def respond(self, input_text, history):
        assert history
        return models.PrivateChatResponse(
            output_text="answer",
            safe_input_text="masked-input",
            safe_output_text="masked-answer",
            guardrails_failed=False,
        )


class _ResponderTripwire:
    def respond(self, input_text, history):
        return models.PrivateChatResponse(
            output_text="申し訳ありません。安全上の理由でこの内容には回答できません。別の聞き方でお試しください。",
            safe_input_text="masked-input",
            safe_output_text="申し訳ありません。安全上の理由でこの内容には回答できません。別の聞き方でお試しください。",
            guardrails_failed=True,
        )


class _ResponderFail:
    def respond(self, input_text, history):
        raise RuntimeError("boom")


def test_private_chat_support_service_pass_flow():
    repo = _Repo()
    service = PrivateChatSupportService(repo=repo, responder=_ResponderPass(), config=PrivateChatSupportConfig(history_limit=5))

    response = service.respond("U123", "hello")

    assert repo.calls == [("U123", 5)]
    assert response.output_text == "answer"
    assert response.safe_input_text == "masked-input"
    assert response.safe_output_text == "masked-answer"
    assert response.guardrails_failed is False


def test_private_chat_support_service_tripwire_response_passthrough():
    repo = _Repo()
    service = PrivateChatSupportService(repo=repo, responder=_ResponderTripwire())

    response = service.respond("U123", "secret")

    assert response.guardrails_failed is True
    assert response.safe_input_text == "masked-input"


def test_private_chat_support_service_returns_fallback_on_exception():
    repo = _Repo()
    service = PrivateChatSupportService(repo=repo, responder=_ResponderFail())

    response = service.respond("U123", "hello")

    assert response.guardrails_failed is True
    assert response.safe_input_text == "hello"
    assert "回答できません" in response.output_text
