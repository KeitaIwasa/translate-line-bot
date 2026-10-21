import json

import requests

from src.infra.gemini_translation import GeminiTranslationAdapter


UI_TEXTS = [
    "Plan: Free (no subscription)",
    "Subscription",
    "Manage your plan. Choose an action below.",
    "Manage billing",
    "Upgrade to Pro",
]


class _Response:
    def __init__(self, status_code=200, text="", body=None):
        self.status_code = status_code
        self._text = text
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError("bad")
            error.response = self
            raise error

    def json(self):
        if self._body is not None:
            return self._body
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}


class _Session:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append({"url": url, "params": params, "json": json, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.response


def _client(session):
    client = GeminiTranslationAdapter(api_key="api-key", model="gemini-flash-latest", timeout_seconds=8)
    client._session = session
    return client


def test_translate_ui_strings_uses_one_structured_call():
    translated = {
        "items": [
            {"id": "0", "text": "プラン: Free（契約なし）"},
            {"id": "1", "text": "サブスクリプション"},
            {"id": "2", "text": "プランを管理します。"},
            {"id": "3", "text": "請求を管理"},
            {"id": "4", "text": "Proにアップグレード"},
        ]
    }
    session = _Session(_Response(text=json.dumps(translated)))
    result = _client(session).translate_ui_strings(UI_TEXTS, "ja")

    assert result == [item["text"] for item in translated["items"]]
    assert len(session.calls) == 1
    body = json.loads(session.calls[0]["json"]["contents"][0]["parts"][0]["text"])
    assert [item["text"] for item in body["items"]] == UI_TEXTS
    assert body["target_language"] == "ja"
    assert session.calls[0]["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_translate_ui_strings_falls_back_to_source_when_parsing_fails():
    session = _Session(_Response(text="not json"))
    result = _client(session).translate_ui_strings(UI_TEXTS, "ja")

    assert result == UI_TEXTS
    assert len(session.calls) == 1


def test_translate_ui_strings_falls_back_when_request_fails():
    session = _Session(exc=requests.Timeout())
    assert _client(session).translate_ui_strings(UI_TEXTS, "ja") == UI_TEXTS

    session = _Session(_Response(status_code=500, body={"error": "nope"}))
    assert _client(session).translate_ui_strings(UI_TEXTS, "ja") == UI_TEXTS


def test_translate_ui_strings_keeps_original_for_missing_ids():
    payload = {"items": [{"id": "1", "text": "サブスクリプション"}]}
    session = _Session(_Response(text=json.dumps(payload)))
    result = _client(session).translate_ui_strings(UI_TEXTS, "ja")

    assert result[1] == "サブスクリプション"
    assert result[0] == UI_TEXTS[0]
    assert result[4] == UI_TEXTS[4]


def test_translate_ui_strings_skips_gemini_for_english():
    session = _Session(_Response(text="{}"))
    assert _client(session).translate_ui_strings(UI_TEXTS, "en") == UI_TEXTS
    assert session.calls == []
