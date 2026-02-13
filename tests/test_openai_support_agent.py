from datetime import datetime, timezone

from src.domain import models
from src.infra.openai_support_agent import OpenAISupportAgent


def _build_agent(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("support prompt", encoding="utf-8")
    return OpenAISupportAgent(
        api_key="dummy",
        support_model="gpt-test",
        guardrail_model="gpt-guardrail",
        prompt_path=str(prompt),
    )


def test_respond_passes_masked_history_text_as_string(tmp_path):
    agent = _build_agent(tmp_path)
    captured_history = []

    agent._run_input_guardrails = lambda text: (text, [])

    def _fake_run_agent(_safe_input, history):
        captured_history.extend(history)
        return "ok"

    agent._run_agent = _fake_run_agent
    history = [
        models.ConversationMessage(
            role="user",
            sender_name="Alice",
            text="my mail is alice@example.com",
            timestamp=datetime.now(timezone.utc),
        )
    ]

    response = agent.respond("hello", history)

    assert response.output_text == "ok"
    assert len(captured_history) == 1
    assert isinstance(captured_history[0].text, str)
    assert captured_history[0].text == "my mail is [EMAIL]"


def test_respond_sets_safe_output_text_as_masked_string(tmp_path):
    agent = _build_agent(tmp_path)
    agent._run_input_guardrails = lambda text: (text, [])
    agent._run_agent = lambda _safe_input, _history: "reply to bob@example.com"

    response = agent.respond("hello", [])

    assert isinstance(response.safe_output_text, str)
    assert response.safe_output_text == "reply to [EMAIL]"
