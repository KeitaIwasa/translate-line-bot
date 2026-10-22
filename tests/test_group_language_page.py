"""グループに紐づく言語設定ページ。署名、未対応コード、空選択、重複、上限。"""

import base64
import time
from urllib.parse import urlencode

from src.app.handlers.join_handler import JoinHandler
from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.domain.services.language_detection_service import LanguageDetectionService
from src.domain.services.language_mention import MentionRead, read_mention
from src.domain.services.language_settings_service import normalize_catalog_selection
from src.group_language_handler import handle_group_language_request
from src.infra.signed_token import issue_token

SECRET = "secret"


class _Repo:
    def __init__(self, records=None, plan=(None, "free")):
        self.records = list(records or [])
        self.plan = plan
        self.replaced = []
        self.reads = []

    def fetch_group_language_records(self, group_id):
        self.reads.append(group_id)
        return list(self.records)

    def replace_group_languages(self, group_id, languages):
        self.replaced.append((group_id, list(languages)))
        self.records = list(languages)

    def get_subscription_plan(self, _group_id):
        status, entitlement = self.plan
        return (status, entitlement, "month", False, None, None, None, None, None, None)


class _Line:
    def __init__(self):
        self.pushed = []

    def push_text(self, to, text):
        self.pushed.append((to, text))


def _token(group_id="G", scope="group_languages", exp=None, secret=SECRET):
    payload = {
        "version": 2,
        "group_id": group_id,
        "scope": scope,
        "exp": int(time.time()) + 3600 if exp is None else exp,
    }
    return issue_token(payload, secret=secret)


def _event(method="GET", token="", body=None, query=None, encoded=False):
    event = {
        "requestContext": {"http": {"method": method}},
        "queryStringParameters": query if query is not None else ({"st": token} if token else {}),
    }
    if body is not None:
        raw = body if isinstance(body, str) else urlencode(body)
        if encoded:
            event["isBase64Encoded"] = True
            event["body"] = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        else:
            event["body"] = raw
    return event


def _post(repo, token, languages, *, line=None, query_token=None, extra=None, encoded=False):
    pairs = [("st", token), ("group_id", "evil-group")]
    for code in languages:
        pairs.append(("languages", code))
    if extra:
        pairs.extend(extra)
    return handle_group_language_request(
        _event(
            "POST",
            body=pairs,
            query={"st": query_token if query_token is not None else token},
            encoded=encoded,
        ),
        repo=repo,
        line=line if line is not None else _Line(),
        secret=SECRET,
    )


def test_normalize_catalog_selection_dedupes_and_rejects_limits():
    deduped = normalize_catalog_selection(["EN", "en", "ja"], limit=3)
    assert deduped.error is None
    assert deduped.languages == (("en", "English"), ("ja", "Japanese"))

    empty = normalize_catalog_selection(["", "  "], limit=3)
    assert empty.error == "empty"

    unsupported = normalize_catalog_selection(["en", "xx"], limit=3)
    assert unsupported.error == "unsupported"
    assert unsupported.unsupported == ("xx",)

    too_many = normalize_catalog_selection(["ja", "en", "zh", "ko"], limit=3)
    assert too_many.error == "too_many"
    assert len(too_many.languages) == 4


def test_get_shows_current_languages_for_the_token_group_only():
    repo = _Repo([("ja", "Japanese"), ("en", "English")])
    token = _token("Cgroup-secret")

    response = handle_group_language_request(
        _event(token=token),
        repo=repo,
        line=_Line(),
        secret=SECRET,
    )

    assert response["statusCode"] == 200
    body = response["body"]
    assert "翻訳言語" in body
    assert "viewport" in body
    assert 'value="ja" checked' in body or 'value="ja" checked' in body.replace("  ", " ")
    assert 'value="en"' in body
    assert "checked" in body
    assert "evil-group" not in body
    assert "Cgroup-secret" not in body
    assert repo.reads == ["Cgroup-secret"]
    assert repo.replaced == []


def test_english_ui_uses_the_base_copy():
    repo = _Repo([("ja", "Japanese")])
    token = _token()
    response = handle_group_language_request(
        _event(token=token, query={"st": token, "ui": "en"}),
        repo=repo,
        line=_Line(),
        secret=SECRET,
    )
    assert "<h1>Translation languages</h1>" in response["body"]
    assert "Choose at least two languages" in response["body"]


def test_missing_and_invalid_tokens_do_not_touch_the_group():
    repo = _Repo([("en", "English")])
    missing = handle_group_language_request(_event(), repo=repo, line=_Line(), secret=SECRET)
    assert missing["statusCode"] == 401
    assert "group link is missing" in missing["body"]

    forged = _token()[:-1] + ("A" if _token()[-1] != "A" else "B")
    rejected = handle_group_language_request(_event(token=forged), repo=repo, line=_Line(), secret=SECRET)
    assert rejected["statusCode"] == 401
    assert "expired" in rejected["body"]

    expired = handle_group_language_request(
        _event(token=_token(exp=int(time.time()) - 10)),
        repo=repo,
        line=_Line(),
        secret=SECRET,
    )
    assert expired["statusCode"] == 401

    other_scope = handle_group_language_request(
        _event(token=_token(scope="support")),
        repo=repo,
        line=_Line(),
        secret=SECRET,
    )
    assert other_scope["statusCode"] == 401
    assert repo.replaced == []
    assert repo.reads == []


def test_checkout_scope_token_can_open_the_same_group_page():
    repo = _Repo([("en", "English")])
    response = handle_group_language_request(
        _event(token=_token(scope="checkout")),
        repo=repo,
        line=_Line(),
        secret=SECRET,
    )
    assert response["statusCode"] == 200
    assert repo.reads == ["G"]


def test_post_rejects_empty_unsupported_and_too_many_without_saving():
    repo = _Repo([("en", "English")])
    line = _Line()
    token = _token()

    empty = _post(repo, token, [], line=line)
    assert empty["statusCode"] == 400
    assert "Choose at least one language" in empty["body"]

    unsupported = _post(repo, token, ["en", "xx"], line=line)
    assert unsupported["statusCode"] == 400
    assert "xx" in unsupported["body"]
    assert "Nothing was changed" in unsupported["body"]

    too_many = _post(repo, token, ["ja", "en", "zh", "ko"], line=line)
    assert too_many["statusCode"] == 400
    assert "up to 3 languages" in too_many["body"]
    assert repo.replaced == []
    assert line.pushed == []
    assert repo.records == [("en", "English")]


def test_post_saves_deduped_languages_and_pushes_once_to_the_token_group():
    repo = _Repo([("en", "English")])
    line = _Line()
    token = _token("G-real")

    response = _post(repo, token, ["JA", "ja", "en"], line=line, encoded=True)

    assert response["statusCode"] == 200
    assert "保存しました" in response["body"] or "Saved." in response["body"]
    assert repo.replaced == [("G-real", [("ja", "Japanese"), ("en", "English")])]
    assert line.pushed[0][0] == "G-real"
    assert "evil-group" not in line.pushed[0][1]
    assert "Updated from the settings page" in line.pushed[0][1]
    assert len(line.pushed) == 1


def test_post_does_not_push_when_the_selection_is_unchanged():
    repo = _Repo([("en", "English"), ("ja", "Japanese")])
    line = _Line()
    response = _post(repo, _token(), ["ja", "en"], line=line)
    assert response["statusCode"] == 200
    assert "already" in response["body"].lower() or "まま" in response["body"]
    assert repo.replaced == []
    assert line.pushed == []


def test_mismatched_tokens_cannot_cross_groups():
    repo = _Repo([("en", "English")])
    own = _token("group-a")
    other = _token("group-b")
    response = _post(repo, other, ["ja", "en"], query_token=own)
    assert response["statusCode"] == 401
    assert repo.replaced == []
    assert repo.reads == []


def test_pro_plan_allows_five_languages_and_rejects_six():
    repo = _Repo([("en", "English")], plan=("active", "pro"))
    line = _Line()
    five = ["ja", "en", "zh", "ko", "th"]
    ok = _post(repo, _token(), five, line=line)
    assert ok["statusCode"] == 200
    assert [code for code, _name in repo.replaced[0][1]] == five

    repo.replaced.clear()
    line.pushed.clear()
    six = _post(repo, _token(), five + ["vi"], line=line)
    assert six["statusCode"] == 400
    assert repo.replaced == []
    assert line.pushed == []


def test_empty_secret_does_not_render_a_form():
    response = handle_group_language_request(_event(token="anything"), repo=_Repo(), line=_Line(), secret="")
    assert response["statusCode"] == 503
    assert "<form" not in response["body"]


def test_settings_and_incomplete_replies_include_the_page_link():
    assert read_mention("設定").kind == "settings_help"
    assert read_mention("言語設定").kind == "settings_help"
    assert read_mention("英語に設定して").kind == "languages"

    class _Subs:
        def create_language_settings_url(self, group_id, *, family=""):
            return f"https://kotori-ai.com/api/group-languages?st=token&ui={family}"

    class _ChatLine:
        def __init__(self):
            self.last_text = None

        def reply_text(self, _token, text):
            self.last_text = text

        def reply_messages(self, *_args, **_kwargs):
            return None

    class _ChatRepo:
        def __init__(self, languages):
            self.languages = list(languages)

        def fetch_group_languages(self, _group_id):
            return list(self.languages)

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

    line = _ChatLine()
    handler = MessageHandler(
        line_client=line,
        translation_service=object(),
        interface_translation=object(),
        language_detector=LanguageDetectionService(),
        language_pref_service=object(),
        command_router=object(),
        repo=_ChatRepo(["ja"]),
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="KOTORI",
        subscription_service=_Subs(),
    )
    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text="設定",
        user_id="U",
        group_id="G",
        sender_type="group",
    )
    handler._handle_local_mention(event, "設定", MentionRead(kind="settings_help"), "ja")
    assert "https://kotori-ai.com/api/group-languages" in (line.last_text or "")
    assert "ui=ja" in (line.last_text or "")

    handler._handle_show_languages(event, "ja")
    assert "https://kotori-ai.com/api/group-languages" in (line.last_text or "")

    handler._repo.languages = ["ja", "en"]
    line.last_text = ""
    handler._handle_show_languages(event, "ja")
    assert "group-languages" not in (line.last_text or "")


def test_join_message_includes_the_language_page_once():
    line = type("Line", (), {"texts": [], "get_group_name": lambda *_: None, "reply_text": lambda self, _t, text: self.texts.append(text)})()
    repo = type(
        "Repo",
        (),
        {
            "record_bot_joined_at": lambda *_: None,
            "upsert_group_name": lambda *_: None,
            "reset_group_language_settings": lambda *_: None,
            "set_translation_enabled": lambda *_: None,
        },
    )()
    handler = JoinHandler(line, repo, language_page_url=lambda group_id: f"https://kotori-ai.com/api/group-languages?st={group_id}")
    handler.handle(
        models.JoinEvent(
            event_type="join",
            reply_token="rpt",
            group_id="G",
            user_id="U",
            sender_type="group",
            timestamp=0,
        )
    )
    text = line.texts[0]
    assert text.count("https://kotori-ai.com/api/group-languages?st=G") == 1
    assert "言語はこのページでも選べるよ" in text
