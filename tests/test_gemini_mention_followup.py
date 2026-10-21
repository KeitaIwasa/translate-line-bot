import json

import requests

from src.domain.models import CommandDecision
from src.infra.gemini_mention_followup import GeminiMentionFollowUp


class _Response:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self._text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        if self._body is not None:
            return self._body
        return {
            "candidates": [
                {"content": {"parts": [{"text": self._text or "{}"}]}}
            ]
        }


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


def _follow_up(session, **kwargs):
    return GeminiMentionFollowUp(
        api_key="gem-key",
        model="gemini-flash-latest",
        timeout_seconds=8,
        session=session,
        **kwargs,
    )


def test_language_settings_follow_up_extracts_languages_only():
    payload = {
        "operation": "add_and_remove",
        "languages_to_add": ["ja", {"code": "en", "name": "English"}],
        "languages_to_remove": [{"code": "th", "name": "Thai"}],
        "instruction_language": "ja",
        "ack_text": "言語を更新します",
    }
    session = _Session(_Response(text=json.dumps(payload)))
    decision = _follow_up(session).complete("language_settings", '{"user_message":"英語を追加"}')

    assert decision == CommandDecision(
        action="language_settings",
        operation="add_and_remove",
        languages_to_add=decision.languages_to_add,
        languages_to_remove=decision.languages_to_remove,
        instruction_language="ja",
        ack_text="言語を更新します",
    )
    assert [item.code for item in decision.languages_to_add] == ["ja", "en"]
    assert [item.code for item in decision.languages_to_remove] == ["th"]
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["key"] == "gem-key"
    assert "language_settings" in session.calls[0]["json"]["systemInstruction"]["parts"][0]["text"]


def test_language_settings_show_operation_is_preserved():
    payload = {
        "operation": "show",
        "languages_to_add": [],
        "languages_to_remove": [],
        "instruction_language": "ja",
        "ack_text": "",
    }
    session = _Session(_Response(text=json.dumps(payload)))
    decision = _follow_up(session).complete("language_settings", '{"user_message":"今の翻訳言語は何?"}')

    assert decision.action == "language_settings"
    assert decision.operation == "show"
    assert decision.instruction_language == "ja"
    assert decision.languages_to_add == []
    assert decision.languages_to_remove == []


def test_howto_follow_up_returns_answer_text():
    payload = {"instruction_language": "en", "ack_text": "Mention the bot and say how to use."}
    session = _Session(_Response(text=json.dumps(payload)))
    decision = _follow_up(session, support_prompt_path="missing-guide.txt").complete("howto", '{"user_message":"help"}')

    assert decision.action == "howto"
    assert decision.ack_text == "Mention the bot and say how to use."
    assert decision.instruction_language == "en"
    assert len(session.calls) == 1


def test_follow_up_parse_or_http_failure_returns_error_without_body_in_decision():
    session = _Session(_Response(text="not-json"))
    decision = _follow_up(session).complete("howto", '{"user_message":"secret question"}')
    assert decision.action == "error"
    assert decision.ack_text == ""
    assert "secret" not in decision.ack_text

    session = _Session(_Response(status_code=500, body={"error": "nope"}))
    assert _follow_up(session).complete("language_settings", "{}").action == "error"


def test_howto_guide_drops_private_chat_preamble(tmp_path):
    from src.infra.gemini_mention_followup import _guide_body

    guide = tmp_path / "guide.txt"
    guide.write_text("- 回答は、KOTORIとユーザーの個人チャットに送信されます。\n\n# KOTORIガイド\n本文", encoding="utf-8")
    body = _guide_body(guide.read_text(encoding="utf-8"))
    assert body.startswith("# KOTORIガイド")
    assert "個人チャット" not in body


def test_howto_prompt_fallback_base_is_english():
    from src.infra.gemini_mention_followup import HOWTO_FALLBACK_TEXT, HOWTO_SYSTEM_PROMPT

    assert HOWTO_FALLBACK_TEXT.isascii()
    assert HOWTO_FALLBACK_TEXT in HOWTO_SYSTEM_PROMPT
    assert "申し訳" not in HOWTO_SYSTEM_PROMPT
