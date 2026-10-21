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


class _FakeFollowUp:
    def __init__(self, decision=None, exc=None):
        self.calls = []
        self.decision = decision or CommandDecision(
            action="language_settings",
            operation="add",
            instruction_language="ja",
            ack_text="追加します",
        )
        self.exc = exc

    def complete(self, action, text):
        self.calls.append((action, text))
        if self.exc:
            raise self.exc
        if self.decision.action == "language_settings" and action == "howto":
            return CommandDecision(action="howto", instruction_language="ja", ack_text="使い方です")
        return self.decision


def _answer(choice, confidence=0.99):
    return _FakeResponse(200, {"answers": {"action": {"type": "choice", "choice": choice, "confidence": confidence}}})


def _router(session, key="ts-key", follow_up=None, **kwargs):
    follow_up = follow_up or _FakeFollowUp()
    router = JevFirstPassCommandRouter(
        follow_up=follow_up,
        api_key_provider=lambda: key,
        session=session,
        **kwargs,
    )
    return router, follow_up


ROUTER_INPUT = json.dumps({"user_message": "翻訳止めて", "translation_enabled": True}, ensure_ascii=False)


def test_template_action_is_decided_by_jev_without_gemini():
    session = _FakeSession(_answer("pause"))
    router, follow_up = _router(session, timeout_seconds=1.5)

    decision = router.decide(ROUTER_INPUT)

    assert decision.action == "pause"
    assert decision.ack_text == ""
    assert follow_up.calls == []
    call = session.calls[0]
    assert call["url"] == "https://api.typesafe.ai/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer ts-key"
    assert call["json"]["state"] == {"message": "翻訳止めて"}
    assert call["json"]["model"] == "jev-latest"
    assert call["json"]["questions"]["action"]["type"] == "choice"
    assert call["timeout"] == 1.5


def test_pause_resume_and_plan_actions_skip_gemini():
    for action in ["pause", "resume", "subscription_menu", "subscription_cancel", "subscription_upgrade"]:
        router, follow_up = _router(_FakeSession(_answer(action)))
        assert router.decide(ROUTER_INPUT).action == action
        assert follow_up.calls == []


def test_six_way_routing():
    direct = {
        "pause": "pause",
        "resume": "resume",
        "subscription_menu": "subscription_menu",
        "subscription_upgrade": "subscription_upgrade",
    }
    for choice, expected in direct.items():
        router, follow_up = _router(_FakeSession(_answer(choice)))
        decision = router.decide(ROUTER_INPUT)
        assert decision.action == expected
        assert follow_up.calls == []

    router, follow_up = _router(_FakeSession(_answer("language_settings")))
    decision = router.decide(ROUTER_INPUT)
    assert decision.action == "language_settings"
    assert decision.operation == "add"
    assert follow_up.calls == [("language_settings", ROUTER_INPUT)]

    router, follow_up = _router(_FakeSession(_answer("howto")))
    decision = router.decide(ROUTER_INPUT)
    assert decision.action == "howto"
    assert decision.ack_text == "使い方です"
    assert follow_up.calls == [("howto", ROUTER_INPUT)]

    router, follow_up = _router(_FakeSession(_answer("unknown")))
    assert router.decide(ROUTER_INPUT).action == "unknown"
    assert follow_up.calls == []

    router, follow_up = _router(_FakeSession(exc=requests.Timeout()))
    assert router.decide(ROUTER_INPUT).action == "error"
    assert follow_up.calls == []


def test_language_and_howto_keep_jev_action_and_call_follow_up_once():
    for action in ["language_settings", "howto"]:
        router, follow_up = _router(_FakeSession(_answer(action)))
        router.decide(ROUTER_INPUT)
        assert follow_up.calls == [(action, ROUTER_INPUT)]


def test_low_confidence_is_unknown_without_gemini():
    router, follow_up = _router(_FakeSession(_answer("pause", 0.5)), min_confidence=0.8)
    assert router.decide(ROUTER_INPUT).action == "unknown"
    assert follow_up.calls == []

    router, follow_up = _router(_FakeSession(_answer("language_settings", 0.5)), min_confidence=0.8)
    assert router.decide(ROUTER_INPUT).action == "unknown"
    assert follow_up.calls == []


def test_missing_key_is_error_without_calling_jev():
    session = _FakeSession(_answer("pause"))
    router, follow_up = _router(session, key="")
    assert router.decide(ROUTER_INPUT).action == "error"
    assert session.calls == []
    assert follow_up.calls == []


def test_timeout_is_error():
    router, _ = _router(_FakeSession(exc=requests.Timeout()))
    assert router.decide(ROUTER_INPUT).action == "error"


def test_http_error_is_error():
    router, _ = _router(_FakeSession(_FakeResponse(401, {"error": "unauthorized"})))
    assert router.decide(ROUTER_INPUT).action == "error"


def test_invalid_response_is_error():
    router, _ = _router(_FakeSession(_FakeResponse(200, {"unexpected": True})))
    assert router.decide(ROUTER_INPUT).action == "error"
    router, _ = _router(_FakeSession(_answer("delete_everything")))
    assert router.decide(ROUTER_INPUT).action == "error"


def test_follow_up_failure_is_error():
    follow_up = _FakeFollowUp(exc=RuntimeError("boom"))
    router, _ = _router(_FakeSession(_answer("howto")), follow_up=follow_up)
    assert router.decide(ROUTER_INPUT).action == "error"

    mismatch = _FakeFollowUp(decision=CommandDecision(action="pause", ack_text="nope"))
    router, _ = _router(_FakeSession(_answer("howto")), follow_up=mismatch)
    assert router.decide(ROUTER_INPUT).action == "error"


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
