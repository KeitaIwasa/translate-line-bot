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


def test_language_settings_prompt_does_not_reset_when_languages_are_unnamed():
    from src.infra.gemini_mention_followup import LANGUAGE_SYSTEM_PROMPT

    prompt = LANGUAGE_SYSTEM_PROMPT
    assert 'does not name languages, use operation "reset_all"' not in prompt
    assert "without naming them" not in prompt
    assert "言語を追加したい" in prompt
    assert 'Use operation "reset_all" only together with explicit_reset true.' in prompt
    from src.infra.gemini_mention_followup import _LANGUAGE_SCHEMA

    assert _LANGUAGE_SCHEMA["properties"]["explicit_reset"]["type"] == "boolean"
    assert "explicit_reset" in _LANGUAGE_SCHEMA["required"]


def test_add_language_without_a_name_is_not_reset_even_if_model_says_so():
    payload = {
        "operation": "reset_all",
        "languages_to_add": [],
        "languages_to_remove": [],
        "instruction_language": "ja",
        "ack_text": "リセットしました",
    }
    session = _Session(_Response(text=json.dumps(payload)))
    decision = _follow_up(session).complete(
        "language_settings",
        json.dumps({"user_message": "言語を追加したい"}, ensure_ascii=False),
    )

    assert decision.operation == "show"
    assert decision.action == "language_settings"


def test_explicit_reset_request_keeps_reset_all():
    payload = {
        "operation": "reset_all",
        "languages_to_add": [],
        "languages_to_remove": [],
        "instruction_language": "ja",
        "ack_text": "リセットします",
    }
    session = _Session(_Response(text=json.dumps(payload)))
    decision = _follow_up(session).complete(
        "language_settings",
        json.dumps({"user_message": "言語設定をリセットして"}, ensure_ascii=False),
    )

    assert decision.operation == "reset_all"
    assert decision.explicit_reset is True


def test_explicit_reset_flag_keeps_reset_for_spanish_and_persian():
    """英語・日本語の語彙が無くても、モデルの explicit_reset があればリセットを残す。"""

    for message in ("borra todos los idiomas", "همه زبان‌ها را پاک کن"):
        payload = {
            "operation": "reset_all",
            "languages_to_add": [],
            "languages_to_remove": [],
            "instruction_language": "es",
            "ack_text": "ok",
            "explicit_reset": True,
        }
        session = _Session(_Response(text=json.dumps(payload)))
        decision = _follow_up(session).complete(
            "language_settings",
            json.dumps({"user_message": message}, ensure_ascii=False),
        )
        assert decision.operation == "reset_all", message
        assert decision.explicit_reset is True


def test_current_languages_question_is_not_reset_without_explicit_flag():
    payload = {
        "operation": "reset_all",
        "explicit_reset": False,
        "instruction_language": "ja",
        "ack_text": "",
    }
    session = _Session(_Response(text=json.dumps(payload)))
    decision = _follow_up(session).complete(
        "language_settings",
        json.dumps({"user_message": "今の翻訳言語は何?"}, ensure_ascii=False),
    )
    assert decision.operation == "show"
    assert decision.explicit_reset is False


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
    prompt = session.calls[0]["json"]["systemInstruction"]["parts"][0]["text"]
    assert '"show"' in prompt or 'operation: "show"' in prompt


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
    assert "as is" in HOWTO_SYSTEM_PROMPT


def test_real_support_guide_drops_private_chat_assumption():
    from pathlib import Path

    from src.infra.gemini_mention_followup import HOWTO_SYSTEM_PROMPT, _guide_body

    guide = Path("src/app/prompts/kotori_support_prompt.txt").read_text(encoding="utf-8")
    body = _guide_body(guide)
    assert "個人チャットに送信" not in body
    assert "すでにユーザーはKOTORIを友だち追加" not in body
    assert "LINE group" in HOWTO_SYSTEM_PROMPT
