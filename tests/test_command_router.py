import json

from src.infra.command_router import OpenAIGroupMentionCommandRouter


def _build_router(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("router prompt", encoding="utf-8")
    return OpenAIGroupMentionCommandRouter(
        api_key="dummy",
        model="gpt-test",
        prompt_path=str(prompt),
        timeout_seconds=1,
    )


def test_decide_parses_language_settings_payload(tmp_path):
    router = _build_router(tmp_path)
    router._run_agent = lambda _text: {
        "action": "language_settings",
        "operation": "add_and_remove",
        "languages_to_add": ["ja", "en"],
        "languages_to_remove": [{"code": "th", "name": "Thai"}],
        "instruction_language": "ja",
        "ack_text": "言語設定を更新します",
    }

    decision = router.decide('{"user_message":"設定変更して"}')

    assert decision.action == "language_settings"
    assert decision.operation == "add_and_remove"
    assert [item.code for item in decision.languages_to_add] == ["ja", "en"]
    assert [item.code for item in decision.languages_to_remove] == ["th"]
    assert decision.instruction_language == "ja"
    assert decision.ack_text == "言語設定を更新します"


def test_decide_parses_stringified_payload(tmp_path):
    router = _build_router(tmp_path)
    router._run_agent = lambda _text: json.dumps(
        {
            "action": "pause",
            "instruction_language": "en",
            "ack_text": "I'll pause translation.",
        },
        ensure_ascii=False,
    )

    decision = router.decide('{"user_message":"pause"}')

    assert decision.action == "pause"
    assert decision.instruction_language == "en"
    assert decision.ack_text == "I'll pause translation."


def test_decide_parses_python_dict_string_payload(tmp_path):
    router = _build_router(tmp_path)
    router._run_agent = lambda _text: "{'action': 'resume', 'instruction_language': 'ja', 'ack_text': '翻訳を再開します。'}"

    decision = router.decide('{"user_message":"resume"}')

    assert decision.action == "resume"
    assert decision.instruction_language == "ja"
    assert decision.ack_text == "翻訳を再開します。"


def test_decide_returns_error_on_agent_exception(tmp_path):
    router = _build_router(tmp_path)

    def _raise(_text):
        raise RuntimeError("boom")

    router._run_agent = _raise

    decision = router.decide('{"user_message":"help"}')

    assert decision.action == "error"
    assert decision.ack_text == ""


def test_decide_returns_error_on_invalid_action(tmp_path):
    router = _build_router(tmp_path)
    router._run_agent = lambda _text: {
        "action": "not_supported",
        "instruction_language": "ja",
        "ack_text": "x",
    }

    decision = router.decide('{"user_message":"x"}')

    assert decision.action == "error"


def test_decide_returns_error_when_tool_is_not_selected(tmp_path):
    router = _build_router(tmp_path)
    router._run_agent = lambda _text: "I can help with that."

    decision = router.decide('{"user_message":"help"}')

    assert decision.action == "error"
