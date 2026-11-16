"""無料枠上限通知の Flex ペイロードと、送信失敗時のテキスト復帰。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.presentation.free_limit_notice import (
    FLEX_ALT_TEXT_LIMIT,
    FLEX_BUTTON_LABEL_LIMIT,
    FREE_LIMIT_BUTTON_LABEL,
    FREE_LIMIT_HEADLINE,
    MAX_FLEX_LANGUAGES,
    build_free_limit_flex_message,
    free_limit_body_text,
)

PLAN_URL = "https://plans.example/pro.html?openExternalBrowser=1&st=token"
BODY_EN = free_limit_body_text(50, "2026-12-01")
BODY_JA = "今月の無料50件を使い切りました。\n2026-12-01 UTC に再開します。"
BODY_KO = "이번 달 무료 50건을 모두 썼습니다.\n2026-12-01 UTC에 재개합니다."
LABELS = {
    "en": "View plans",
    "ja": "プランを見る",
    "ko": "요금제 보기",
}
HEADLINES = {
    "en": "Free limit reached",
    "ja": "無料枠の上限です",
    "ko": "무료 한도에 도달했습니다",
}
BODIES = {"en": BODY_EN, "ja": BODY_JA, "ko": BODY_KO}


def _texts(message: dict) -> list[str]:
    found: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                found.append(node["text"])
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(message.get("contents"))
    return found


def _action(message: dict) -> dict:
    return message["contents"]["footer"]["contents"][0]["action"]


def test_flex_payload_for_en_ja_ko_uses_button_instead_of_raw_url():
    message = build_free_limit_flex_message(
        headlines=[HEADLINES["en"], HEADLINES["ja"], HEADLINES["ko"]],
        bodies=[BODIES["en"], BODIES["ja"], BODIES["ko"]],
        button_labels=[LABELS["en"], LABELS["ja"], LABELS["ko"]],
        url=PLAN_URL,
    )

    assert message is not None
    assert message["type"] == "flex"
    assert message["contents"]["type"] == "bubble"
    assert message["altText"] == f"{HEADLINES['en']}. This month's 50 free messages are used up."
    assert len(message["altText"]) <= FLEX_ALT_TEXT_LIMIT
    assert PLAN_URL not in message["altText"]

    action = _action(message)
    assert action == {
        "type": "uri",
        "label": "View plans / プランを見る / 요금제 보기",
        "uri": PLAN_URL,
    }
    assert len(action["label"]) <= FLEX_BUTTON_LABEL_LIMIT

    rendered = _texts(message)
    assert rendered[0] == HEADLINES["en"]
    assert BODY_EN in rendered
    assert BODY_JA in rendered
    assert BODY_KO in rendered
    assert all(PLAN_URL not in text for text in rendered)
    assert message["contents"]["body"]["contents"][0]["weight"] == "bold"


def test_alt_text_uses_the_first_group_language():
    message = build_free_limit_flex_message(
        headlines=[HEADLINES["ja"], HEADLINES["en"], HEADLINES["ko"]],
        bodies=[BODIES["ja"], BODIES["en"], BODIES["ko"]],
        button_labels=[LABELS["ja"], LABELS["en"], LABELS["ko"]],
        url=PLAN_URL,
    )

    assert message is not None
    assert message["altText"].startswith(HEADLINES["ja"])
    assert "今月の無料50件を使い切りました。" in message["altText"]


def test_button_label_falls_back_to_the_first_language_when_joined_label_is_too_long():
    message = build_free_limit_flex_message(
        headlines=["Free limit reached"] * 4,
        bodies=[f"Body {index}" for index in range(4)],
        button_labels=["View plans now please", "プランを確認する", "요금제 바로 보기", "查看目前的方案"],
        url=PLAN_URL,
    )

    assert message is not None
    label = _action(message)["label"]
    assert label == "View plans now please"
    assert len(label) <= FLEX_BUTTON_LABEL_LIMIT


def test_many_languages_stay_within_the_bubble_cap_and_skip_duplicate_english():
    headlines = [FREE_LIMIT_HEADLINE] * 8
    bodies = [BODY_EN] + [f"Body {index}" for index in range(7)]
    message = build_free_limit_flex_message(
        headlines=headlines,
        bodies=bodies,
        button_labels=["View plans"] * 8,
        url=PLAN_URL,
    )

    assert message is not None
    body_texts = [text for text in _texts(message) if text != FREE_LIMIT_HEADLINE]
    assert len(body_texts) == MAX_FLEX_LANGUAGES
    assert body_texts[0] == BODY_EN
    assert _action(message)["label"] == FREE_LIMIT_BUTTON_LABEL


def test_identical_translations_are_not_repeated():
    message = build_free_limit_flex_message(
        headlines=[FREE_LIMIT_HEADLINE, FREE_LIMIT_HEADLINE],
        bodies=[BODY_EN, BODY_EN],
        button_labels=["View plans", "View plans"],
        url=PLAN_URL,
    )

    assert message is not None
    assert _texts(message).count(BODY_EN) == 1


def test_body_is_kept_to_two_short_lines():
    long_body = "First sentence is intentionally long. " * 8 + "\nSecond\nThird should join the second line"
    message = build_free_limit_flex_message(
        headlines=["Free limit reached"],
        bodies=[long_body],
        button_labels=["View plans"],
        url=PLAN_URL,
    )

    assert message is not None
    body = _texts(message)[1]
    assert body.count("\n") == 1
    assert "…" in body


def test_invalid_or_missing_url_does_not_build_flex():
    args = {
        "headlines": [FREE_LIMIT_HEADLINE],
        "bodies": [BODY_EN],
        "button_labels": [FREE_LIMIT_BUTTON_LABEL],
    }
    assert build_free_limit_flex_message(**args, url="") is None
    assert build_free_limit_flex_message(**args, url="javascript:alert(1)") is None
    assert build_free_limit_flex_message(**args, url="https://plans.example/" + ("a" * 1200)) is None


def test_alt_text_stays_within_the_line_limit():
    message = build_free_limit_flex_message(
        headlines=["H" * 200],
        bodies=["B" * 200],
        button_labels=["View plans"],
        url=PLAN_URL,
    )

    assert message is not None
    assert len(message["altText"]) <= FLEX_ALT_TEXT_LIMIT
    assert message["altText"].startswith("H" * 40)


class _Line:
    def __init__(self, *, fail_times: int = 0):
        self.batches: list[list[dict]] = []
        self.fail_times = fail_times

    def reply_text(self, _token, text):
        self.reply_messages(_token, [{"type": "text", "text": text}])

    def reply_messages(self, _token, messages):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("invalid payload")
        self.batches.append(list(messages))


class _Repo:
    def __init__(self, languages: list[str]):
        self.languages = languages
        self.notice_plan: str | None = None

    def fetch_group_languages(self, _group_id):
        return list(self.languages)

    def get_limit_notice_plan(self, _group_id, _period_key):
        return self.notice_plan

    def claim_limit_notice_plan(self, _group_id, _period_key, plan: str) -> bool:
        if self.notice_plan == plan:
            return False
        self.notice_plan = plan
        return True

    def release_limit_notice_plan(self, _group_id, _period_key, plan: str) -> None:
        if self.notice_plan == plan:
            self.notice_plan = None

    def set_limit_notice_plan(self, _group_id, _period_key, plan: str) -> None:
        self.notice_plan = plan


class _UI:
    def translate(self, base_text, target_languages):
        results = []
        for lang in target_languages:
            if lang == "ko" and base_text == BODY_EN:
                continue
            text = {
                FREE_LIMIT_HEADLINE: HEADLINES.get(lang, FREE_LIMIT_HEADLINE),
                BODY_EN: BODIES.get(lang, BODY_EN),
                FREE_LIMIT_BUTTON_LABEL: LABELS.get(lang, FREE_LIMIT_BUTTON_LABEL),
            }.get(base_text, base_text)
            results.append(models.TranslationResult(lang=lang, text=text))
        return results


class _Plans:
    def __init__(self, url: str | None = PLAN_URL):
        self.url = url

    def create_checkout_url(self, _group_id):
        return self.url


def _handler(line: _Line, languages: list[str], *, url: str | None = PLAN_URL) -> tuple[MessageHandler, _Repo]:
    repo = _Repo(languages)
    handler = cast(Any, MessageHandler)(
        line_client=line,
        translation_service=object(),
        interface_translation=_UI(),
        language_detector=None,
        language_pref_service=object(),
        command_router=object(),
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="bot",
        subscription_service=_Plans(url),  # type: ignore[arg-type]
    )
    return handler, repo


def _event() -> models.MessageEvent:
    return models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="group",
        user_id="user",
        sender_type="group",
        timestamp=0,
        text="hello",
    )


def test_handler_builds_multilingual_flex_and_falls_back_to_english_text():
    line = _Line()
    handler, _repo = _handler(line, ["en", "ja", "ko"])

    handler._send_limit_reached_notice(_event(), "free", 50, period_key="2026-11-01")

    flex = line.batches[0][0]
    assert flex["type"] == "flex"
    rendered = "\n".join(_texts(flex))
    assert BODY_JA in rendered
    assert BODY_EN in rendered
    assert "2026-12-01" in rendered
    # ko の本文翻訳が無いので英語本文を使い、同じ文面は重ねない
    assert rendered.count(BODY_EN) == 1
    assert "요금제" not in rendered
    assert _action(flex)["uri"] == PLAN_URL
    assert "プランを見る" in _action(flex)["label"]


def test_missing_plan_url_keeps_the_plain_text_notice():
    line = _Line()
    handler, _repo = _handler(line, ["en"], url=None)

    handler._send_limit_reached_notice(_event(), "free", 50, period_key="2026-11-01")

    assert line.batches[0][0]["type"] == "text"
    assert "Free quota" in line.batches[0][0]["text"]
    assert "Unable to generate purchase link" in line.batches[0][0]["text"]


def test_text_fallback_keeps_the_claim_and_double_failure_releases_it():
    line = _Line(fail_times=1)
    handler, repo = _handler(line, ["ja"])

    handler._maybe_send_limit_notice(_event(), 50, "free", "2026-11-01")

    assert repo.notice_plan == "free"
    assert line.batches[0][0]["type"] == "text"
    assert "Free quota" in line.batches[0][0]["text"]
    assert "LANG:" not in line.batches[0][0]["text"]
    assert line.batches[0][1]["text"] == PLAN_URL

    line.fail_times = 2
    repo.notice_plan = None
    with pytest.raises(RuntimeError, match="invalid payload"):
        handler._maybe_send_limit_notice(_event(), 50, "free", "2026-11-01")
    assert repo.notice_plan is None


def test_standard_and_pro_notices_stay_plain_text():
    line = _Line()
    handler, _repo = _handler(line, ["en", "ja"])

    handler._send_limit_reached_notice(_event(), "standard", 4000, period_key="2026-11-01")
    handler._send_limit_reached_notice(
        _event(),
        "pro",
        40000,
        period_end=datetime(2026, 12, 1, tzinfo=UTC),
    )

    assert line.batches[0][0]["type"] == "text"
    assert "Standard plan" in line.batches[0][0]["text"]
    assert line.batches[0][1]["text"] == PLAN_URL
    assert line.batches[1][0]["type"] == "text"
    assert "Pro plan" in line.batches[1][0]["text"]
    assert len(line.batches[1]) == 1
