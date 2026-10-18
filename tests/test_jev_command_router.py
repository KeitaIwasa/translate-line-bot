import json

import requests

from src.domain.models import CommandDecision
from src.infra.jev_command_router import JevFirstPassCommandRouter, TypesafeApiKeyProvider


class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.response


class _FakeFallback:
    def __init__(self):
        self.inputs = []

    def decide(self, text):
        self.inputs.append(text)
        return CommandDecision(action="language_settings", operation="add", instruction_language="ja", ack_text="LLM")


def _answer(choice, confidence=0.99):
    return _FakeResponse(200, {"answers": {"action": {"type": "choice", "choice": choice, "confidence": confidence}}})


def _router(session, key="ts-key", **kwargs):
    fallback = _FakeFallback()
    router = JevFirstPassCommandRouter(
        fallback=fallback,
        api_key_provider=lambda: key,
        session=session,
        **kwargs,
    )
    return router, fallback


ROUTER_INPUT = json.dumps({"user_message": "翻訳止めて", "translation_enabled": True}, ensure_ascii=False)


def test_template_action_is_decided_by_jev_without_llm():
    session = _FakeSession(_answer("pause"))
    router, fallback = _router(session, timeout_seconds=1.5)

    decision = router.decide(ROUTER_INPUT)

    assert decision.action == "pause"
    assert decision.ack_text == ""
    assert fallback.inputs == []
    call = session.calls[0]
    assert call["url"] == "https://api.typesafe.ai/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer ts-key"
    assert call["json"]["state"] == {"message": "翻訳止めて"}
    assert call["json"]["model"] == "jev-latest"
    assert call["json"]["questions"]["action"]["type"] == "choice"
    assert call["timeout"] == 1.5


def test_all_template_actions_skip_llm():
    for action in ["pause", "resume", "subscription_menu", "subscription_cancel", "subscription_upgrade"]:
        router, fallback = _router(_FakeSession(_answer(action)))
        assert router.decide(ROUTER_INPUT).action == action
        assert fallback.inputs == []


def test_language_dependent_actions_fall_through_to_llm():
    for action in ["language_settings", "howto", "unknown"]:
        router, fallback = _router(_FakeSession(_answer(action)))
        decision = router.decide(ROUTER_INPUT)
        assert decision.ack_text == "LLM"
        assert fallback.inputs == [ROUTER_INPUT]


def test_low_confidence_falls_back():
    router, fallback = _router(_FakeSession(_answer("pause", 0.5)), min_confidence=0.8)
    assert router.decide(ROUTER_INPUT).ack_text == "LLM"
    assert fallback.inputs == [ROUTER_INPUT]


def test_missing_key_falls_back_without_calling_jev():
    session = _FakeSession(_answer("pause"))
    router, fallback = _router(session, key="")
    assert router.decide(ROUTER_INPUT).ack_text == "LLM"
    assert session.calls == []


def test_timeout_falls_back():
    router, fallback = _router(_FakeSession(exc=requests.Timeout()))
    assert router.decide(ROUTER_INPUT).ack_text == "LLM"


def test_http_error_falls_back():
    router, fallback = _router(_FakeSession(_FakeResponse(401, {"error": "unauthorized"})))
    assert router.decide(ROUTER_INPUT).ack_text == "LLM"


def test_invalid_response_falls_back():
    router, fallback = _router(_FakeSession(_FakeResponse(200, {"unexpected": True})))
    assert router.decide(ROUTER_INPUT).ack_text == "LLM"
    router, fallback = _router(_FakeSession(_answer("delete_everything")))
    assert router.decide(ROUTER_INPUT).ack_text == "LLM"


def test_non_json_router_input_is_sent_as_message():
    session = _FakeSession(_answer("resume"))
    router, _ = _router(session)
    assert router.decide("resume please").action == "resume"
    assert session.calls[0]["json"]["state"] == {"message": "resume please"}


def test_key_provider_prefers_env_value():
    provider = TypesafeApiKeyProvider(api_key=" env-key ", ssm_parameter_name="/kotori/stg/x",
                                      ssm_client_factory=lambda: (_ for _ in ()).throw(AssertionError()))
    assert provider() == "env-key"


def test_key_provider_reads_ssm_once():
    calls = []

    class _Ssm:
        def get_parameter(self, Name, WithDecryption):
            calls.append((Name, WithDecryption))
            return {"Parameter": {"Value": "ssm-key\n"}}

    provider = TypesafeApiKeyProvider(ssm_parameter_name="/kotori/stg/typesafe-api-key", ssm_client_factory=_Ssm)
    assert provider() == "ssm-key"
    assert provider() == "ssm-key"
    assert calls == [("/kotori/stg/typesafe-api-key", True)]


def test_key_provider_returns_empty_on_ssm_error():
    class _Ssm:
        def get_parameter(self, Name, WithDecryption):
            raise RuntimeError("denied")

    provider = TypesafeApiKeyProvider(ssm_parameter_name="/kotori/stg/typesafe-api-key", ssm_client_factory=_Ssm)
    assert provider() == ""


def test_key_provider_without_sources_is_empty():
    assert TypesafeApiKeyProvider()() == ""
