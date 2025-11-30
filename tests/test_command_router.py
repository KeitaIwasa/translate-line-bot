from requests import HTTPError

from src.infra.command_router import GeminiCommandRouter


def test_decide_returns_unknown_on_http_error(monkeypatch):
    router = GeminiCommandRouter(api_key="dummy", model="dummy", timeout_seconds=1)

    class DummyResponse:
        status_code = 500
        text = "error"

        def raise_for_status(self):
            raise HTTPError("boom")

    def fake_post(*_args, **_kwargs):
        return DummyResponse()

    monkeypatch.setattr(router._session, "post", fake_post)

    decision = router.decide("翻訳停止して")

    assert decision.action == "unknown"
    assert decision.ack_text == ""

