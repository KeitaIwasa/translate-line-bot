"""案内・コマンド返信の Flex と、送信失敗時の平文フォールバック。"""

import json
import logging

import pytest

from src.app.handlers.member_joined_handler import MemberJoinedHandler
from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.infra.line_api import LineApiAdapter
from src.presentation.guidance_cards import (
    _LABELS,
    billing_notice_card,
    catalog_card,
    howto_card,
    join_card,
    language_reset_card,
    localized_label,
    member_joined_card,
    quota_notice_card,
    settings_help_card,
    unknown_command_card,
)
from src.presentation.rich_message import (
    ALT_TEXT_LIMIT,
    FLEX_BUTTON_LABEL_LIMIT,
    QUICK_REPLY_LABEL_LIMIT,
    GuidanceMessage,
    RichButton,
    deliver_push,
    deliver_reply,
    flex_body_text,
    render_guidance,
    utf16_len,
)
from src.domain.services.language_mention import catalog_prompt, howto_prompt, settings_help_prompt


class _FailingFlexLine:
    def __init__(self) -> None:
        self.calls = []

    def reply_messages(self, _token, messages):
        self.calls.append(messages)
        if messages and messages[0].get("type") == "flex":
            raise RuntimeError("flex rejected")

    def push_messages(self, to, messages):
        self.calls.append((to, messages))
        if messages and messages[0].get("type") == "flex":
            raise RuntimeError("flex rejected")


def _button_uris(message: dict) -> list[str]:
    footer = (message.get("contents") or {}).get("footer") or {}
    uris = []
    for component in footer.get("contents") or []:
        action = component.get("action") or {}
        if action.get("uri"):
            uris.append(action["uri"])
    return uris


def _quick_replies(message: dict) -> list[dict]:
    return [item.get("action") or {} for item in (message.get("quickReply") or {}).get("items") or []]


def test_builder_hides_url_behind_button_and_keeps_alt_text():
    guidance = GuidanceMessage(
        title="Plans",
        body="Free quota is exhausted.\n\n翻訳は止まります。",
        buttons=(RichButton(label="View plans", uri="https://kotori.example/pro?st=secret-token"),),
        plain_text="Free quota is exhausted.\n\nhttps://kotori.example/pro?st=secret-token",
    )
    rendered = render_guidance(guidance)
    flex = rendered.flex
    assert flex is not None
    assert flex["type"] == "flex"
    assert flex["altText"]
    assert "http" not in flex["altText"]
    assert len(flex["altText"]) <= ALT_TEXT_LIMIT
    assert "Free quota is exhausted." in flex["altText"]
    body = flex_body_text(flex)
    assert "Free quota is exhausted." in body
    assert "翻訳は止まります。" in body
    assert "http" not in body
    assert _button_uris(flex) == ["https://kotori.example/pro?st=secret-token"]
    assert rendered.plain_messages[0]["text"].startswith("Free quota is exhausted.")
    assert rendered.plain_messages[1]["text"] == "https://kotori.example/pro?st=secret-token"


def test_builder_truncates_labels_alt_text_and_quick_replies():
    long_label = "あ" * 80
    guidance = GuidanceMessage(
        body="x" * 500,
        alt_text="preview " + ("語" * 500) + " https://example.com/very-long",
        buttons=(RichButton(label=long_label, uri="https://example.com/plans"),),
        quick_replies=tuple(
            RichButton(label="り" * 30, text=f"@KOTORI command {index}") for index in range(20)
        ),
    )
    rendered = render_guidance(guidance)
    flex = rendered.flex
    assert flex is not None
    action = flex["contents"]["footer"]["contents"][0]["action"]
    assert utf16_len(action["label"]) == FLEX_BUTTON_LABEL_LIMIT
    assert "http" not in flex["altText"]
    assert utf16_len(flex["altText"]) <= ALT_TEXT_LIMIT
    assert flex["altText"].endswith("…")
    replies = _quick_replies(flex)
    assert len(replies) == 13
    assert all(utf16_len(item["label"]) <= QUICK_REPLY_LABEL_LIMIT for item in replies)


def test_builder_keeps_rtl_cjk_and_long_text():
    arabic = "مرحبا بالعالم. هذه رسالة طويلة للتجربة."
    hebrew = "שלום. זו הודעה ארוכה לבדיקה."
    cjk = "漢字" * 1300
    body = f"{arabic}\n\nこんにちは、言語を設定してください。\n\n{hebrew}\n\n{cjk}"
    rendered = render_guidance(GuidanceMessage(body=body))
    flex = rendered.flex
    assert flex is not None
    assert flex_body_text(flex) == body
    contents = flex["contents"]["body"]["contents"]
    assert contents[0]["align"] == "end"
    assert "align" not in contents[1]
    assert contents[2]["align"] == "end"
    chunks = []
    cjk_component = contents[3]
    assert cjk_component["type"] == "box"
    for child in cjk_component["contents"]:
        assert utf16_len(child["text"]) <= 2000
        chunks.append(child["text"])
    assert "".join(chunks) == cjk


def test_builder_omits_flex_when_uri_or_payload_is_too_large():
    long_uri = "https://example.com/" + ("a" * 1200)
    rendered = render_guidance(
        GuidanceMessage(
            body="Open the plans page.",
            buttons=(RichButton(label="View plans", uri=long_uri),),
            plain_text=f"Open the plans page.\n\n{long_uri}",
        )
    )
    assert rendered.flex is None
    assert rendered.omit_reason == "uri_too_long"
    assert any(long_uri in message["text"] for message in rendered.plain_messages)

    huge = "文" * 20000
    rendered = render_guidance(GuidanceMessage(body=huge))
    assert rendered.flex is None
    assert rendered.omit_reason == "payload_too_large"
    assert rendered.plain_messages[0]["text"].startswith("文")
    assert len(rendered.plain_messages[0]["text"]) <= 5000


def test_builder_supports_postback_and_drops_unsafe_uri():
    rendered = render_guidance(
        GuidanceMessage(
            body="Confirm languages.",
            buttons=(
                RichButton(label="OK", data="language_confirm"),
                RichButton(label="Bad", uri="javascript:alert(1)"),
            ),
        )
    )
    flex = rendered.flex
    assert flex is not None
    actions = [item["action"] for item in flex["contents"]["footer"]["contents"]]
    assert actions == [{"type": "postback", "label": "OK", "data": "language_confirm"}]


def test_fixed_labels_fit_line_limits():
    for key, options in _LABELS.items():
        limit = QUICK_REPLY_LABEL_LIMIT if key.startswith("qr_") else FLEX_BUTTON_LABEL_LIMIT
        for _prefix, text in options:
            assert text
            assert utf16_len(text) <= limit, key


@pytest.mark.parametrize(
    ("lang", "snippet"),
    [
        ("en", "Add Thai"),
        ("ja", "タイ語を追加して"),
        ("zh", "添加泰语"),
        ("ko", "태국어 추가해 줘"),
        ("th", "เพิ่มไทย"),
    ],
)
def test_howto_card_keeps_each_language(lang, snippet):
    rendered = render_guidance(howto_card(howto_prompt(family=lang), lang=lang))
    flex = rendered.flex
    assert flex is not None
    assert snippet in flex_body_text(flex)
    assert flex["contents"]["header"]["contents"][1]["text"] == localized_label("how_to_use", lang)
    assert "footer" not in flex["contents"]


def test_howto_card_moves_contact_url_to_a_button():
    text = "Sorry, I can't answer that question. Please contact us here: https://kotori-ai.com/contact.html"
    rendered = render_guidance(howto_card(text, lang="ja"))
    flex = rendered.flex
    assert flex is not None
    body = flex_body_text(flex)
    assert "http" not in body
    assert "answer that question" in body
    assert body.endswith("here.")
    ja = flex_body_text(
        render_guidance(howto_card("詳しくはこちら: https://kotori-ai.com/contact.html", lang="ja")).flex
    )
    assert ja == "詳しくはこちら"
    assert "http" not in ja
    assert _button_uris(flex) == ["https://kotori-ai.com/contact.html"]
    assert flex["contents"]["footer"]["contents"][0]["action"]["label"] == "お問い合わせ"
    assert "https://kotori-ai.com/contact.html" in rendered.plain_messages[-1]["text"]


def test_howto_card_splits_inline_numbered_steps():
    rendered = render_guidance(
        howto_card(
            "1. Add KOTORI as a friend on LINE. 2. Invite KOTORI to the LINE group you want to use. 3. Enter the languages.",
            lang="en",
        )
    )
    body = flex_body_text(rendered.flex)
    assert body == (
        "1. Add KOTORI as a friend on LINE.\n"
        "2. Invite KOTORI to the LINE group you want to use.\n"
        "3. Enter the languages."
    )


def test_join_and_member_joined_cards_keep_every_language():
    join = render_guidance(join_card()).flex
    join_body = flex_body_text(join)
    for snippet in (
        "I'm a multilingual translation bot.",
        "多言語翻訳ボットです。",
        "我是一个多语言翻译机器人。",
        "ฉันเป็นบอทแปลหลายภาษา",
        "English, 中文, 日本語, ไทย",
    ):
        assert snippet in join_body
    assert "http" not in join_body

    joined = render_guidance(member_joined_card("アキ、Ben")).flex
    joined_body = flex_body_text(joined)
    assert joined_body.startswith("Hello アキ、Ben !")
    assert "remove this bot from the group once" in joined_body
    assert "再度招待してね" in joined_body
    assert "然后再重新邀请它" in joined_body
    assert "เชิญกลับมาอีกครั้ง" in joined_body


def test_settings_catalog_reset_and_unknown_cards():
    settings = render_guidance(
        settings_help_card(settings_help_prompt(["英語", "日本語"], family="ja", limit=3), lang="ja")
    ).flex
    settings_body = flex_body_text(settings)
    assert "今の翻訳言語は英語・日本語だよ。" in settings_body
    assert "タイ語を追加して" in settings_body
    assert "韓国語を外して" in settings_body

    catalog = render_guidance(catalog_card(catalog_prompt(family="ja", limit=3), lang="ja")).flex
    catalog_body = flex_body_text(catalog)
    assert "対応している言語はこれだよ。" in catalog_body
    assert "スワヒリ語" in catalog_body
    assert "最大3つまで選べるよ。" in catalog_body

    reset = render_guidance(
        language_reset_card(
            "Your language settings have been reset. Please tell us all the languages you would like to translate.",
            lang="en",
        )
    ).flex
    assert "have been reset" in flex_body_text(reset)

    unknown = render_guidance(
        unknown_command_card(
            "To interact with this bot, please mention it again and provide one of the following commands:\n"
            "- Change language settings\n- How to use\n- Stop translation\n- Subscription management",
            lang="ja",
            bot_name="通訳AI",
        )
    ).flex
    assert "Change language settings" in flex_body_text(unknown)
    replies = _quick_replies(unknown)
    assert [item["label"] for item in replies] == ["使い方", "言語設定", "翻訳停止", "プラン"]
    assert replies[0]["text"] == "@通訳AI 使い方"
    assert replies[1]["text"] == "@通訳AI 言語設定の変更"
    assert replies[3]["text"] == "@通訳AI サブスクリプション"


def test_quota_and_billing_cards_localize_buttons_without_raw_urls():
    url = "https://frontend.example.com/pro.html?openExternalBrowser=1&st=signed"
    arabic_body = "تم استنفاد الحصة المجانية.\n\nالترجمة متوقفة الآن."
    quota = render_guidance(quota_notice_card(arabic_body, lang="ar", url=url)).flex
    assert "http" not in flex_body_text(quota)
    assert flex_body_text(quota) == arabic_body
    assert quota["contents"]["body"]["contents"][0]["align"] == "end"
    action = quota["contents"]["footer"]["contents"][0]["action"]
    assert action["label"] == "عرض الخطط"
    assert action["uri"] == url

    no_link = render_guidance(
        quota_notice_card("The Pro plan monthly limit has been reached and translation has stopped.", lang="en", url=None)
    ).flex
    assert "footer" not in no_link["contents"]
    assert "stopped" in flex_body_text(no_link)

    billing = render_guidance(
        billing_notice_card("The billing owner has left.\n\n請求の管理が必要です。", lang="ja", url=url)
    ).flex
    assert "http" not in flex_body_text(billing)
    assert billing["contents"]["footer"]["contents"][0]["action"]["label"] == "支払いを管理"
    assert "https://frontend.example.com/pro.html?openExternalBrowser=1&st=signed" in billing["contents"]["footer"]["contents"][0]["action"]["uri"]


def test_deliver_reply_and_push_fall_back_without_logging_the_url(caplog):
    line = _FailingFlexLine()
    guidance = quota_notice_card(
        "Free quota is exhausted.",
        lang="en",
        url="https://kotori.example/pro?st=secret-token",
    )
    caplog.set_level(logging.WARNING, logger="tests.test_rich_guidance")
    # deliver は呼び出し側のロガーへ出す
    test_logger = logging.getLogger("tests.rich-guidance-fallback")
    with caplog.at_level(logging.WARNING, logger="tests.rich-guidance-fallback"):
        sent = deliver_reply(line, "token", guidance, logger=test_logger)
        deliver_push(line, "G1", guidance, logger=test_logger)

    assert sent[0]["type"] == "text"
    assert "secret-token" not in sent[0]["text"]
    assert sent[1]["text"] == "https://kotori.example/pro?st=secret-token"
    assert line.calls[1][0]["type"] == "text"
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "falling back to plain text" in logs
    assert "secret-token" not in logs


def test_push_failure_log_does_not_include_signed_url(caplog):
    class _Session:
        def __init__(self) -> None:
            self.payload = None
            self.headers = {}

        def post(self, url, json, timeout):  # noqa: A002
            self.payload = (url, json, timeout)
            return type("Response", (), {"ok": False, "status_code": 400, "text": "secret-token echoed"})()

    adapter = LineApiAdapter("dummy")
    adapter._session = _Session()  # type: ignore[assignment]
    rendered = render_guidance(
        billing_notice_card("Owner left.", lang="en", url="https://example.com/pro?st=secret-token")
    )
    caplog.set_level(logging.WARNING, logger="src.infra.line_api")
    with pytest.raises(RuntimeError):
        adapter.push_messages("G1", [rendered.flex])

    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "secret-token" not in logs
    assert "types=['flex']" in logs
    assert adapter._session.payload[1]["messages"][0]["type"] == "flex"  # type: ignore[attr-defined]


def test_member_joined_sends_compact_guidance():
    class _Line:
        def __init__(self) -> None:
            self.messages = None

        def reply_messages(self, _token, messages):
            self.messages = messages

        def get_display_name(self, *_args, **_kwargs):
            return "アキ"

    class _Repo:
        def fetch_bot_joined_at(self, _group_id):
            return None

        def ensure_group_member(self, *_args, **_kwargs):
            return None

    line = _Line()
    handler = MemberJoinedHandler(line, _Repo())
    handler.handle(
        models.MemberJoinedEvent(
            event_type="memberJoined",
            reply_token="token",
            group_id="G",
            user_id="U",
            sender_type="group",
            joined_user_ids=["U2"],
            timestamp=0,
        )
    )
    flex = line.messages[0]
    assert flex["type"] == "flex"
    assert "Hello アキ !" in flex_body_text(flex)
    assert "http" not in flex_body_text(flex)


class _NoticeRepo:
    def fetch_group_languages(self, _group_id):
        return ["ar", "ja"]


class _NoticeLine:
    def __init__(self) -> None:
        self.messages = None

    def reply_messages(self, _token, messages):
        self.messages = messages


def test_limit_notice_handler_sends_flex_button_for_each_paid_tier():
    line = _NoticeLine()
    handler = MessageHandler(
        line_client=line,
        translation_service=_NoticeRepo(),
        interface_translation=_NoticeRepo(),
        language_detector=_NoticeRepo(),
        language_pref_service=_NoticeRepo(),
        command_router=_NoticeRepo(),
        repo=_NoticeRepo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="通訳AI",
        subscription_frontend_base_url="https://frontend.example.com",
        checkout_api_base_url="https://api.example.com",
    )
    handler._build_multilingual_interface_message = lambda base, _gid: (  # type: ignore[method-assign]
        f"{base}\n\n"
        "تم استنفاد الحصة المجانية وتتوقف الترجمة الآن حتى إعادة التعيين\n\n"
        f"日本語: {base}"
    )
    handler._subscription_service.create_checkout_url = lambda _gid: "https://frontend.example.com/pro.html?st=secret-token"  # type: ignore[attr-defined]
    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G",
        user_id="U",
        sender_type="group",
        text="hello",
        timestamp=0,
    )

    handler._send_limit_reached_notice(event, "free", 50)
    flex = line.messages[0]
    body = flex_body_text(flex)
    assert flex["type"] == "flex"
    assert "Free quota" in body
    assert "تم استنفاد الحصة المجانية" in body
    assert "日本語:" in body
    assert "secret-token" not in body
    assert flex["contents"]["body"]["contents"][1]["align"] == "end"
    assert flex["contents"]["footer"]["contents"][0]["action"]["label"] == "عرض الخطط"
    assert flex["contents"]["footer"]["contents"][0]["action"]["uri"].endswith("st=secret-token")

    handler._send_limit_reached_notice(event, "standard", 4000)
    assert "upgrade to the Pro plan" in flex_body_text(line.messages[0])
    assert "secret-token" not in flex_body_text(line.messages[0])

    handler._subscription_service.create_checkout_url = lambda _gid: None  # type: ignore[attr-defined]
    handler._send_limit_reached_notice(event, "pro", 40000)
    pro = line.messages[0]
    assert "translation has stopped" in flex_body_text(pro)
    assert "footer" not in pro["contents"]
    assert "http" not in json.dumps(pro)


class _CommandRepo:
    def __init__(self) -> None:
        self.languages = ["ja"]
        self.enabled = []

    def fetch_translation_runtime_state(self, _group_id):
        return models.TranslationRuntimeState(
            translation_enabled=True,
            group_languages=list(self.languages),
            subscription_status=None,
            period_start=None,
            period_end=None,
            period_key="2026-10",
            usage=0,
            limit_notice_plan=None,
            entitlement_plan="free",
        )

    def fetch_group_languages(self, _group_id):
        return list(self.languages)

    def fetch_group_language_records(self, _group_id):
        return [("ja", "日本語")]

    def set_translation_enabled(self, _group_id, enabled):
        self.enabled.append(enabled)


class _CommandLine:
    def __init__(self) -> None:
        self.messages = None

    def reply_messages(self, _token, messages):
        self.messages = messages

    def reply_text(self, _token, text):
        self.messages = [{"type": "text", "text": text}]


class _HowtoRouter:
    def decide(self, _text):
        return models.CommandDecision(
            action="howto",
            instruction_language="en",
            ack_text="Please contact us here: https://kotori-ai.com/contact.html",
        )


def test_howto_command_reply_is_a_flex_message():
    line = _CommandLine()
    handler = MessageHandler(
        line_client=line,
        translation_service=_CommandRepo(),
        interface_translation=_CommandRepo(),
        language_detector=_CommandRepo(),
        language_pref_service=_CommandRepo(),
        command_router=_HowtoRouter(),
        repo=_CommandRepo(),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="KOTORI",
    )
    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        group_id="G",
        user_id="U",
        sender_type="group",
        text="@KOTORI what about billing details?",
        timestamp=0,
    )
    assert handler._handle_command(event, "what about billing details?") is True
    flex = line.messages[0]
    assert flex["type"] == "flex"
    assert "http" not in flex_body_text(flex)
    assert _button_uris(flex) == ["https://kotori-ai.com/contact.html"]
