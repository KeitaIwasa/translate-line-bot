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
    FLEX_HEADLINE_LIMIT,
    FREE_LIMIT_BUTTON_LABEL,
    FREE_LIMIT_HEADLINE,
    MAX_FLEX_LANGUAGES,
    accept_notice_translation,
    build_free_limit_flex_message,
    free_limit_body_text,
    static_notice_body,
    static_notice_button,
    static_notice_headline,
)

PLAN_URL = "https://plans.example/pro.html?openExternalBrowser=1&st=token"
BODY_EN = free_limit_body_text(50, "2026-12-01")


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


def _flex_for(languages: list[str], *, limit: int = 50, reset_date: str = "2026-12-01") -> dict:
    first = languages[0]
    message = build_free_limit_flex_message(
        headline=static_notice_headline(first) or "",
        bodies=[static_notice_body(lang, limit, reset_date) or "" for lang in languages],
        button_label=static_notice_button(first) or "",
        url=PLAN_URL,
    )
    assert message is not None
    return message


def test_flex_json_for_en_ja_ko_and_ja_en_ko():
    en_body = (
        "This month's 50 free messages are used up.\n"
        "Translation resumes on 2026-12-01 UTC."
    )
    ja_body = (
        "今月の無料メッセージ50件を使い切りました。\n"
        "2026-12-01 UTC に翻訳が再開します。"
    )
    ko_body = (
        "이번 달 무료 메시지 50건을 모두 사용했습니다.\n"
        "2026-12-01 UTC에 번역이 다시 시작됩니다."
    )
    en_first = _flex_for(["en", "ja", "ko"])
    ja_first = _flex_for(["ja", "en", "ko"])

    assert en_first == {
        "type": "flex",
        "altText": "Free limit reached. This month's 50 free messages are used up.",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "Free limit reached", "weight": "bold", "size": "md", "wrap": True},
                    {"type": "text", "text": en_body, "size": "sm", "wrap": True},
                    {"type": "separator"},
                    {"type": "text", "text": ja_body, "size": "sm", "wrap": True},
                    {"type": "separator"},
                    {"type": "text", "text": ko_body, "size": "sm", "wrap": True},
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "action": {"type": "uri", "label": "Remove limit", "uri": PLAN_URL},
                    }
                ],
            },
        },
    }
    assert ja_first["altText"] == "無料枠の上限です 今月の無料メッセージ50件を使い切りました。"
    assert _texts(ja_first) == [
        "無料枠の上限です",
        ja_body,
        en_body,
        ko_body,
    ]
    assert _action(ja_first)["label"] == "上限を解除する"
    assert _action(ja_first)["uri"] == PLAN_URL
    assert " / " not in _action(en_first)["label"]
    assert " / " not in _action(ja_first)["label"]
    assert PLAN_URL not in ja_first["altText"]


@pytest.mark.parametrize(
    "language",
    ["en", "ja", "ko", "zh", "zh-cn", "zh-TW", "zh-Hant", "th", "vi", "id", "es", "fr", "pt", "de"],
)
def test_static_copy_is_short_and_includes_the_reset_date(language):
    headline = static_notice_headline(language) or ""
    button = static_notice_button(language) or ""
    body = static_notice_body(language, 50, "2026-12-01") or ""

    assert headline
    assert button
    assert " / " not in button
    assert len(button) <= 20
    assert len(headline) <= FLEX_HEADLINE_LIMIT
    assert "2026-12-01" in body
    for line in body.split("\n"):
        assert len(line) <= 90


def test_runaway_or_wrong_script_translation_falls_back_to_english():
    source = free_limit_body_text(50, "2026-12-01")
    garbage = "篝月づぶっどというやうか 朧月づぶっどというやうかというか"
    assert accept_notice_translation(source, garbage, "ja") == source
    assert accept_notice_translation(source, source * 4, "ko") == source
    assert accept_notice_translation(FREE_LIMIT_BUTTON_LABEL, "See the plans", "my") == FREE_LIMIT_BUTTON_LABEL
    kept = "이번 달 무료 메시지 50건을 모두 사용했습니다."
    assert accept_notice_translation(source, kept, "ko") == kept


def test_button_label_stays_within_the_line_limit():
    message = build_free_limit_flex_message(
        headline="Free limit reached",
        bodies=["Body"],
        button_label="V" * 80,
        url=PLAN_URL,
    )

    assert message is not None
    assert len(_action(message)["label"]) == FLEX_BUTTON_LABEL_LIMIT
    assert _action(message)["label"].endswith("…")


def test_many_languages_stay_within_the_bubble_cap_and_skip_duplicate_english():
    bodies = [BODY_EN] + [f"Body {index}" for index in range(7)]
    message = build_free_limit_flex_message(
        headline=FREE_LIMIT_HEADLINE,
        bodies=bodies,
        button_label=FREE_LIMIT_BUTTON_LABEL,
        url=PLAN_URL,
    )

    assert message is not None
    body_texts = [text for text in _texts(message) if text != FREE_LIMIT_HEADLINE]
    assert len(body_texts) == MAX_FLEX_LANGUAGES
    assert body_texts[0] == BODY_EN
    assert _action(message)["label"] == FREE_LIMIT_BUTTON_LABEL


def test_identical_translations_are_not_repeated():
    message = build_free_limit_flex_message(
        headline=FREE_LIMIT_HEADLINE,
        bodies=[BODY_EN, BODY_EN],
        button_label=FREE_LIMIT_BUTTON_LABEL,
        url=PLAN_URL,
    )

    assert message is not None
    assert _texts(message).count(BODY_EN) == 1


def test_body_is_kept_to_two_short_lines():
    long_body = "First sentence is intentionally long. " * 8 + "\nSecond\nThird should join the second line"
    message = build_free_limit_flex_message(
        headline="Free limit reached",
        bodies=[long_body],
        button_label=FREE_LIMIT_BUTTON_LABEL,
        url=PLAN_URL,
    )

    assert message is not None
    body = _texts(message)[1]
    assert body.count("\n") == 1
    assert "…" in body


def test_invalid_or_missing_url_does_not_build_flex():
    args = {
        "headline": FREE_LIMIT_HEADLINE,
        "bodies": [BODY_EN],
        "button_label": FREE_LIMIT_BUTTON_LABEL,
    }
    assert build_free_limit_flex_message(**args, url="") is None
    assert build_free_limit_flex_message(**args, url="javascript:alert(1)") is None
    assert build_free_limit_flex_message(**args, url="https://plans.example/" + ("a" * 1200)) is None


def test_alt_text_stays_within_the_line_limit():
    message = build_free_limit_flex_message(
        headline="H" * 200,
        bodies=["B" * 200],
        button_label=FREE_LIMIT_BUTTON_LABEL,
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
    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def translate(self, base_text, target_languages):
        languages = list(target_languages)
        self.calls.append((base_text, languages))
        results = []
        for lang in languages:
            if lang == "my" and base_text == FREE_LIMIT_BUTTON_LABEL:
                text = "ကန့်သတ်ချက်ဖယ်ရှားရန်"
            elif lang == "my":
                text = "ဒီလ အခမဲ့စာ ၅၀ ပြီးပါပြီ။"
            else:
                text = base_text
            results.append(models.TranslationResult(lang=lang, text=text))
        return results


class _Plans:
    def __init__(self, url: str | None = PLAN_URL):
        self.url = url

    def create_checkout_url(self, _group_id):
        return self.url


def _handler(
    line: _Line,
    languages: list[str],
    *,
    url: str | None = PLAN_URL,
    ui: _UI | None = None,
) -> tuple[MessageHandler, _Repo, _UI]:
    repo = _Repo(languages)
    translator = ui or _UI()
    handler = cast(Any, MessageHandler)(
        line_client=line,
        translation_service=object(),
        interface_translation=translator,
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
    return handler, repo, translator


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


def test_handler_uses_static_copy_for_en_ja_ko_without_calling_the_translator():
    line = _Line()
    handler, _repo, ui = _handler(line, ["en", "ja", "ko"])
    handler._send_limit_reached_notice(_event(), "free", 50, period_key="2026-11-01")
    assert ui.calls == []
    assert line.batches[0][0] == _flex_for(["en", "ja", "ko"])

    line = _Line()
    handler, _repo, ui = _handler(line, ["ja", "en", "ko"])
    handler._send_limit_reached_notice(_event(), "free", 50, period_key="2026-11-01")
    assert ui.calls == []
    assert line.batches[0][0] == _flex_for(["ja", "en", "ko"])
    assert _action(line.batches[0][0])["label"] == "上限を解除する"


def test_unlisted_language_drops_wrong_script_and_keeps_a_matching_script():
    line = _Line()
    handler, _repo, ui = _handler(line, ["my"])
    handler._send_limit_reached_notice(_event(), "free", 50, period_key="2026-11-01")

    assert ui.calls
    flex = line.batches[0][0]
    rendered = "\n".join(_texts(flex))
    assert "LANG:" not in rendered
    assert "ဒီလ အခမဲ့စာ" in rendered
    assert _action(flex)["label"] == "ကန့်သတ်ချက်ဖယ်ရှားရန်"


def test_missing_plan_url_keeps_the_plain_text_notice():
    line = _Line()
    handler, _repo, _ui = _handler(line, ["en"], url=None)

    handler._send_limit_reached_notice(_event(), "free", 50, period_key="2026-11-01")

    assert line.batches[0][0]["type"] == "text"
    assert "This month's 50 free messages are used up." in line.batches[0][0]["text"]
    assert "Unable to generate purchase link" in line.batches[0][0]["text"]
    assert len(line.batches[0]) == 1


def test_text_fallback_keeps_the_claim_and_double_failure_releases_it():
    line = _Line(fail_times=1)
    handler, repo, _ui = _handler(line, ["ja"])

    handler._maybe_send_limit_notice(_event(), 50, "free", "2026-11-01")

    assert repo.notice_plan == "free"
    assert line.batches[0][0]["type"] == "text"
    assert "今月の無料メッセージ50件を使い切りました。" in line.batches[0][0]["text"]
    assert "LANG:" not in line.batches[0][0]["text"]
    assert line.batches[0][1]["text"] == PLAN_URL

    line.fail_times = 2
    repo.notice_plan = None
    with pytest.raises(RuntimeError, match="invalid payload"):
        handler._maybe_send_limit_notice(_event(), 50, "free", "2026-11-01")
    assert repo.notice_plan is None


def test_standard_and_pro_notices_stay_plain_text():
    line = _Line()
    handler, _repo, _ui = _handler(line, ["en", "ja"])

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
