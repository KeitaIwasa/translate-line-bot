import json

import pytest
import requests

from src.app.handlers.message_handler import MessageHandler
from src.domain import models
from src.domain.models import LanguageChoice
from src.domain.services.language_detection_service import LanguageDetectionService
from src.infra.gemini_mention_followup import GeminiMentionFollowUp
from src.infra.jev_command_router import JevFirstPassCommandRouter
from src.infra.jev_language_followup import JevLanguageMentionFollowUp
from src.infra.jev_language_reader import LANGUAGE_CATALOG, LANGUAGE_SETTINGS_ACK_BASE, confirmation_ack
from src.infra.language_pref_client import LanguagePreferenceAdapter

ROUTER_INPUT = json.dumps(
    {"user_message": "英語を追加", "current_languages": ["ja", "bad code"]},
    ensure_ascii=False,
)


class _Response:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._body


class JevSession:
    def __init__(
        self,
        *,
        operation="add",
        operation_confidence=0.99,
        language="ja",
        language_confidence=0.99,
        yes_codes=None,
        remove_codes=None,
        outside=0.01,
        noul_overrides=None,
        drop_keys=None,
        exc=None,
        status=200,
    ):
        self.operation = operation
        self.operation_confidence = operation_confidence
        self.language = language
        self.language_confidence = language_confidence
        self.yes_codes = set(yes_codes or [])
        self.remove_codes = set(remove_codes or [])
        self.outside = outside
        self.noul_overrides = noul_overrides or {}
        self.drop_keys = set(drop_keys or [])
        self.exc = exc
        self.status = status
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None, params=None):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout, "params": params}
        )
        if self.exc:
            raise self.exc
        if self.status != 200:
            return _Response(self.status, {"error": "nope"})
        answers = {}
        for key, question in json["questions"].items():
            if key in self.drop_keys:
                continue
            if question["type"] == "choice":
                if key == "operation":
                    choice, confidence = self.operation, self.operation_confidence
                else:
                    choice, confidence = self.language, self.language_confidence
                answers[key] = {"type": "choice", "choice": choice, "confidence": confidence}
            else:
                if key in self.noul_overrides:
                    value = self.noul_overrides[key]
                elif key == "outside_catalog":
                    value = self.outside
                elif key.startswith("remove_"):
                    value = 0.99 if key.removeprefix("remove_") in self.remove_codes else 0.01
                else:
                    code = key.split("_", 1)[1]
                    value = 0.99 if code in self.yes_codes else 0.01
                answers[key] = {"type": "noul", "noul": value}
        return _Response(200, {"answers": answers})


class GeminiSession:
    def __init__(self, text="{}", status=200):
        self.text = text
        self.status = status
        self.calls = []

    def post(self, url, params=None, json=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": params, "json": json, "timeout": timeout})
        if self.status != 200:
            return _Response(self.status, {"error": "nope"})
        return _Response(200, {"candidates": [{"content": {"parts": [{"text": self.text}]}}]})


def _gemini_language_settings(ack="フランス語を追加します"):
    return json.dumps(
        {
            "operation": "add",
            "languages_to_add": [{"code": "fr", "name": "French"}],
            "languages_to_remove": [],
            "instruction_language": "ja",
            "ack_text": ack,
        }
    )


def _follow_up(jev, gemini_text=_gemini_language_settings(), **kwargs):
    gemini = GeminiMentionFollowUp(
        api_key="gem-key",
        model="gemini-flash-latest",
        session=GeminiSession(text=gemini_text),
    )
    follow_up = JevLanguageMentionFollowUp(
        gemini=gemini,
        api_key_provider=kwargs.pop("api_key_provider", lambda: "ts-key"),
        session=jev,
        **kwargs,
    )
    return follow_up, gemini


def test_language_settings_are_read_by_jev_without_gemini():
    jev = JevSession(yes_codes={"en"})
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert decision.action == "language_settings"
    assert decision.operation == "add"
    assert decision.languages_to_add == [LanguageChoice(code="en", name="English")]
    assert decision.languages_to_remove == []
    assert decision.instruction_language == "ja"
    assert decision.ack_text == "言語設定を更新しました。"
    assert gemini._session.calls == []
    call = jev.calls[0]
    assert call["url"] == "https://api.typesafe.ai/v1/systemone"
    assert call["headers"]["Authorization"] == "Bearer ts-key"
    assert call["json"]["model"] == "jev-latest"
    assert call["timeout"] == 1.5
    assert call["json"]["state"] == {"message": "英語を追加", "current_languages": ["ja", "bad code"]}
    questions = call["json"]["questions"]
    assert questions["operation"]["type"] == "choice"
    assert questions["add_en"]["type"] == "noul"
    assert questions["remove_ja"]["type"] == "noul"
    assert "remove_fr" not in questions
    assert not any(" " in key for key in questions)
    assert len([key for key in questions if key.startswith("add_")]) == len(LANGUAGE_CATALOG)


def test_add_and_remove_keeps_both_lists_in_stable_order():
    jev = JevSession(operation="add_and_remove", yes_codes={"en", "ja"}, remove_codes={"ja"})
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert [item.code for item in decision.languages_to_add] == ["ja", "en"]
    assert [item.code for item in decision.languages_to_remove] == ["ja"]
    assert gemini._session.calls == []


def test_reset_all_clears_language_lists_only_when_explicit():
    jev = JevSession(operation="reset_all", noul_overrides={"explicit_reset": 0.99})
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert decision.operation == "reset_all"
    assert decision.explicit_reset is True
    assert decision.languages_to_add == []
    assert decision.languages_to_remove == []
    assert gemini._session.calls == []


def test_reset_without_explicit_request_returns_show():
    jev = JevSession(operation="reset_all")
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert decision.operation == "show"
    assert decision.explicit_reset is False
    assert decision.action == "language_settings"
    assert decision.languages_to_add == []
    assert decision.languages_to_remove == []
    assert decision.ack_text == ""
    assert gemini._session.calls == []


def test_show_operation_keeps_languages_unchanged():
    jev = JevSession(operation="show", language="ja")
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert decision.operation == "show"
    assert decision.instruction_language == "ja"
    assert decision.languages_to_add == []
    assert decision.languages_to_remove == []
    assert decision.ack_text == ""
    criteria = jev.calls[0]["json"]["questions"]["operation"]["criteria"]
    assert "show" in criteria
    assert "explicit" in criteria["reset_all"].lower()
    assert "explicit_reset" in jev.calls[0]["json"]["questions"]
    assert gemini._session.calls == []


def test_explicit_reset_follows_confidence_threshold():
    jev = JevSession(operation="reset_all", noul_overrides={"explicit_reset": 0.8})
    follow_up, gemini = _follow_up(jev)
    decision = follow_up.complete("language_settings", ROUTER_INPUT)
    assert decision.operation == "reset_all"
    assert decision.explicit_reset is True
    assert gemini._session.calls == []

    jev = JevSession(operation="reset_all", noul_overrides={"explicit_reset": 0.79})
    follow_up, gemini = _follow_up(jev)
    decision = follow_up.complete("language_settings", ROUTER_INPUT)
    assert decision.operation == "show"
    assert decision.explicit_reset is False
    assert gemini._session.calls == []


# 文面ごとの Jev の答え。explicit_reset が閾値未満の reset_all は show になる
_JEV_PHRASE_ANSWERS = {
    "borra todos los idiomas": {"operation": "reset_all", "noul_overrides": {"explicit_reset": 0.99}},
    "言語を追加したい": {"operation": "reset_all", "noul_overrides": {"explicit_reset": 0.01}},
    "今の翻訳言語は何?": {"operation": "show", "noul_overrides": {"explicit_reset": 0.01}},
}


class _PhraseJevSession:
    """分類は language_settings。設定の答えは文面ごとに固定する。"""

    def __init__(self, extra_answers=None):
        self.extra_answers = extra_answers or {}

    def post(self, url, headers=None, json=None, timeout=None, params=None):
        message = (json or {}).get("state", {}).get("message")
        questions = (json or {}).get("questions") or {}
        if "action" in questions:
            return _Response(
                200,
                {
                    "answers": {
                        "action": {
                            "type": "choice",
                            "choice": "language_settings",
                            "confidence": 0.99,
                        }
                    }
                },
            )
        answers = {**_JEV_PHRASE_ANSWERS, **self.extra_answers}
        settings = JevSession(**answers[message])
        return settings.post(url, headers=headers, json=json, timeout=timeout, params=params)


class _Line:
    def __init__(self):
        self.last_text = None

    def reply_text(self, _token, text):
        self.last_text = text


class _Translator:
    def translate(self, **kwargs):
        lang = kwargs["candidate_languages"][0]
        return [models.TranslationResult(lang=lang, text=f"[{lang}]{kwargs['message_text']}")]


class _Repo:
    def __init__(self):
        self.languages = ["ja", "en"]
        self.reset_called = False
        self.enabled = []

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
        )

    def set_translation_enabled(self, _group_id, enabled):
        self.enabled.append(enabled)

    def add_group_languages(self, _group_id, languages):
        for code, _name in languages:
            if code not in self.languages:
                self.languages.append(code)

    def reset_group_language_settings(self, *_args):
        self.reset_called = True

    def is_translation_enabled(self, *_args):
        return True

    def get_usage(self, *_args, **_kwargs):
        return 0

    def get_subscription_period(self, *_args):
        return (None, None, None)


def _handler_with_jev(repo, session):
    gemini = GeminiMentionFollowUp(
        api_key="gem-key",
        model="gemini-flash-latest",
        session=GeminiSession(text=_gemini_language_settings()),
    )
    follow_up = JevLanguageMentionFollowUp(
        gemini=gemini,
        api_key_provider=lambda: "ts-key",
        session=session,
    )
    router = JevFirstPassCommandRouter(
        follow_up=follow_up,
        api_key_provider=lambda: "ts-key",
        session=session,
    )
    line = _Line()
    handler = MessageHandler(
        line_client=line,
        translation_service=_Translator(),
        interface_translation=_Translator(),
        language_detector=LanguageDetectionService(),
        language_pref_service=object(),
        command_router=router,
        repo=repo,
        max_context_messages=1,
        max_group_languages=5,
        translation_retry=1,
        bot_mention_name="通訳AI",
    )
    return handler, gemini, line


def test_jev_path_resets_only_when_explicit_reset_clears_the_threshold():
    """スペイン語の全消去は Jev の explicit_reset をハンドラーまで伝える。追加希望と現状の質問は消さない。"""

    cases = (
        ("borra todos los idiomas", True),
        ("言語を追加したい", False),
        ("今の翻訳言語は何?", False),
    )
    for text, should_reset in cases:
        repo = _Repo()
        handler, gemini, _line = _handler_with_jev(repo, _PhraseJevSession())
        event = models.MessageEvent(
            event_type="message",
            reply_token="token",
            timestamp=0,
            text=text,
            user_id="U",
            group_id="G",
            sender_type="group",
            destination="BOT",
            mentionees=[],
        )

        handler._handle_command(event, text)

        assert repo.reset_called is should_reset, text
        assert gemini._session.calls == [], text
        if should_reset:
            assert False in repo.enabled
        else:
            assert repo.enabled == []


def test_uncertain_message_language_leaves_ack_for_the_handler_to_translate():
    """書き言葉が低信頼でも、日本語の追加依頼は英語の確認文に固定しない。"""

    text = "英語を追加して"
    session = _PhraseJevSession(
        {
            text: {
                "operation": "add",
                "language_confidence": 0.2,
                "yes_codes": {"en"},
            }
        }
    )
    repo = _Repo()
    repo.languages = ["ja"]
    handler, gemini, line = _handler_with_jev(repo, session)
    event = models.MessageEvent(
        event_type="message",
        reply_token="token",
        timestamp=0,
        text=text,
        user_id="U",
        group_id="G",
        sender_type="group",
        destination="BOT",
        mentionees=[],
    )

    handler._handle_command(event, text)

    assert gemini._session.calls == []
    assert "en" in repo.languages
    assert line.last_text == "[ja]Language settings have been updated."
    assert line.last_text != LANGUAGE_SETTINGS_ACK_BASE


def test_confirmation_ack_stays_empty_when_the_message_language_is_unknown():
    assert confirmation_ack("") == ""
    assert confirmation_ack("en") == LANGUAGE_SETTINGS_ACK_BASE
    assert confirmation_ack("ja") == "言語設定を更新しました。"
    assert confirmation_ack("fr") == ""


def test_show_with_a_named_language_falls_back_to_gemini():
    jev = JevSession(operation="show", yes_codes={"en"})
    follow_up, gemini = _follow_up(jev)

    assert follow_up.complete("language_settings", ROUTER_INPUT).ack_text == "フランス語を追加します"
    assert len(gemini._session.calls) == 1


def test_reset_with_a_named_language_falls_back_to_gemini():
    jev = JevSession(operation="reset_all", yes_codes={"en"})
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert decision.ack_text == "フランス語を追加します"
    assert len(gemini._session.calls) == 1


def test_mixed_add_and_remove_falls_back_to_gemini():
    jev = JevSession(operation="add", yes_codes={"en"}, remove_codes={"ja"})
    follow_up, gemini = _follow_up(jev)

    assert follow_up.complete("language_settings", ROUTER_INPUT).ack_text == "フランス語を追加します"
    assert len(gemini._session.calls) == 1


def test_low_confidence_outside_catalog_and_empty_add_fall_back():
    cases = [
        JevSession(operation_confidence=0.5, yes_codes={"en"}),
        JevSession(yes_codes={"en"}, outside=0.91),
        JevSession(operation="add"),
        JevSession(yes_codes={"en"}, noul_overrides={"add_en": 0.79}),
        JevSession(yes_codes={"en"}, drop_keys={"add_fr"}),
        JevSession(status=500, yes_codes={"en"}),
        JevSession(exc=requests.Timeout(), yes_codes={"en"}),
    ]
    for jev in cases:
        follow_up, gemini = _follow_up(jev)
        decision = follow_up.complete("language_settings", ROUTER_INPUT)
        assert decision.ack_text == "フランス語を追加します"
        assert len(gemini._session.calls) == 1


def test_threshold_language_is_kept():
    jev = JevSession(noul_overrides={"add_en": 0.8})
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert [item.code for item in decision.languages_to_add] == ["en"]
    assert gemini._session.calls == []


def test_catalog_language_without_static_ack_leaves_translation_to_handler():
    jev = JevSession(language="fr", yes_codes={"fr"})
    follow_up, gemini = _follow_up(jev)
    decision = follow_up.complete("language_settings", ROUTER_INPUT)
    assert decision.instruction_language == "fr"
    assert decision.languages_to_add == [LanguageChoice(code="fr", name="French")]
    assert decision.ack_text == ""
    assert gemini._session.calls == []

    jev = JevSession(language="es", yes_codes={"es"})
    follow_up, gemini = _follow_up(jev)
    decision = follow_up.complete("language_settings", ROUTER_INPUT)
    assert decision.instruction_language == "es"
    assert decision.ack_text == ""
    assert gemini._session.calls == []

    jev = JevSession(language="other", language_confidence=0.99, yes_codes={"en"})
    follow_up, gemini = _follow_up(jev)
    decision = follow_up.complete("language_settings", ROUTER_INPUT)
    assert decision.instruction_language == ""
    assert decision.ack_text == ""
    assert gemini._session.calls == []


def test_low_instruction_confidence_keeps_languages():
    jev = JevSession(language_confidence=0.2, yes_codes={"en"})
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", ROUTER_INPUT)

    assert decision.instruction_language == ""
    assert decision.ack_text == ""
    assert [item.code for item in decision.languages_to_add] == ["en"]
    assert gemini._session.calls == []


def test_missing_jev_key_skips_http_and_uses_gemini():
    jev = JevSession(yes_codes={"en"})
    follow_up, gemini = _follow_up(jev, api_key_provider=lambda: "")
    assert follow_up.complete("language_settings", ROUTER_INPUT).operation == "add"
    assert jev.calls == []
    assert len(gemini._session.calls) == 1

    def _boom():
        raise RuntimeError("ssm down")

    jev = JevSession(yes_codes={"en"})
    follow_up, gemini = _follow_up(jev, api_key_provider=_boom)
    assert follow_up.complete("language_settings", ROUTER_INPUT).ack_text == "フランス語を追加します"
    assert jev.calls == []


def test_howto_stays_on_gemini():
    jev = JevSession()
    gemini = GeminiMentionFollowUp(
        api_key="gem-key",
        model="gemini-flash-latest",
        session=GeminiSession(text=json.dumps({"instruction_language": "en", "ack_text": "help"})),
    )
    follow_up = JevLanguageMentionFollowUp(
        gemini=gemini,
        api_key_provider=lambda: "ts-key",
        session=jev,
    )

    decision = follow_up.complete("howto", ROUTER_INPUT)

    assert decision.action == "howto"
    assert decision.ack_text == "help"
    assert jev.calls == []
    assert len(gemini._session.calls) == 1


def test_catalog_code_outside_current_languages_can_be_removed_when_present():
    payload = json.dumps(
        {"user_message": "繁体字を外して", "current_languages": ["zh-tw", "ja"]},
        ensure_ascii=False,
    )
    jev = JevSession(operation="remove", remove_codes={"zh-tw"})
    follow_up, gemini = _follow_up(jev)

    decision = follow_up.complete("language_settings", payload)

    assert decision.languages_to_remove == [LanguageChoice(code="zh-tw", name="zh-tw")]
    assert "remove_zh-tw" in jev.calls[0]["json"]["questions"]
    assert gemini._session.calls == []


class HybridSession(JevSession):
    def __init__(self, gemini_text="{}", gemini_status=200, **kwargs):
        super().__init__(**kwargs)
        self.gemini_text = gemini_text
        self.gemini_status = gemini_status
        self.gemini_calls = []

    def post(self, url, headers=None, json=None, timeout=None, params=None):
        if url and "generativelanguage.googleapis.com" in url:
            self.gemini_calls.append({"url": url, "params": params, "json": json, "timeout": timeout})
            if self.gemini_status != 200:
                return _Response(self.gemini_status, {"error": "nope"})
            return _Response(
                200,
                {"candidates": [{"content": {"parts": [{"text": self.gemini_text}]}}]},
            )
        return super().post(url, headers=headers, json=json, timeout=timeout, params=params)


_ENROLLMENT_GEMINI = json.dumps(
    {
        "primaryLanguage": "ja",
        "languages": [
            {"code": "sa", "supported": False, "display": {"primary": "Sanskrit"}},
            {"code": "en", "supported": True, "display": {"primary": "English"}},
        ],
        "buttonLabels": {"confirm": "完了", "cancel": "変更する"},
    }
)


def _adapter(session, **kwargs):
    return LanguagePreferenceAdapter(
        "gem-key",
        "gemini-flash-latest",
        jev_api_key_provider=kwargs.pop("jev_api_key_provider", lambda: "ts-key"),
        session=session,
        **kwargs,
    )


def test_enrollment_reads_languages_from_jev():
    session = JevSession(language="ja", yes_codes={"en", "th"})
    preference = _adapter(session).analyze("英語とタイ語")

    assert [item.code for item in preference.supported] == ["en", "th"]
    assert [item.name for item in preference.supported] == ["English", "Thai"]
    assert preference.unsupported == []
    assert preference.primary_language == "ja"
    assert preference.confirm_label == "OK"
    assert preference.cancel_label == "Cancel"
    assert len(session.calls) == 1
    assert session.calls[0]["json"]["state"] == {"message": "英語とタイ語"}
    assert "want_en" in session.calls[0]["json"]["questions"]
    assert "operation" not in session.calls[0]["json"]["questions"]


def test_enrollment_confident_empty_does_not_call_gemini():
    session = HybridSession(language="ja", gemini_text=_ENROLLMENT_GEMINI)
    assert _adapter(session).analyze("hello") is None
    assert len(session.calls) == 1
    assert session.gemini_calls == []


def test_enrollment_near_threshold_falls_back_to_gemini():
    """英語 0.79 のように閾値直下は言語なしにせず、初期設定を Gemini に任せる。"""

    session = HybridSession(
        language="ja",
        noul_overrides={"want_en": 0.79},
        gemini_text=_ENROLLMENT_GEMINI,
    )
    preference = _adapter(session).analyze("英語")

    assert [item.code for item in preference.supported] == ["en"]
    assert len(session.calls) == 1
    assert len(session.gemini_calls) == 1


def test_enrollment_barely_positive_score_falls_back_to_gemini():
    session = HybridSession(
        language="ja",
        noul_overrides={"want_en": 0.21},
        gemini_text=_ENROLLMENT_GEMINI,
    )
    preference = _adapter(session).analyze("英語")

    assert [item.code for item in preference.supported] == ["en"]
    assert len(session.gemini_calls) == 1


def test_enrollment_clearly_negative_scores_skip_gemini():
    session = HybridSession(
        language="ja",
        noul_overrides={"want_en": 0.2, "want_ja": 0.2},
        gemini_text=_ENROLLMENT_GEMINI,
    )

    assert _adapter(session).analyze("hello") is None
    assert len(session.calls) == 1
    assert session.gemini_calls == []


def test_blank_enrollment_text_skips_both_models():
    session = HybridSession()
    assert _adapter(session).analyze("  ") is None
    assert session.calls == []
    assert session.gemini_calls == []


def test_enrollment_falls_back_to_gemini_for_uncertain_or_outside_languages():
    cases = [
        HybridSession(language="ja", yes_codes={"en"}, outside=0.9, gemini_text=_ENROLLMENT_GEMINI),
        HybridSession(language="other", yes_codes={"en"}, gemini_text=_ENROLLMENT_GEMINI),
        HybridSession(language="ja", language_confidence=0.4, yes_codes={"en"}, gemini_text=_ENROLLMENT_GEMINI),
        HybridSession(status=500, gemini_text=_ENROLLMENT_GEMINI),
        HybridSession(exc=requests.Timeout(), gemini_text=_ENROLLMENT_GEMINI),
    ]
    for session in cases:
        preference = _adapter(session).analyze("サンスクリットと英語")
        assert [item.code for item in preference.supported] == ["en"]
        assert [item.code for item in preference.unsupported] == ["sa"]
        assert preference.confirm_label == "完了"
        assert len(session.gemini_calls) == 1


def test_enrollment_without_jev_provider_stays_on_gemini():
    session = HybridSession(gemini_text=_ENROLLMENT_GEMINI)
    adapter = LanguagePreferenceAdapter("gem-key", "gemini-flash-latest", session=session)
    preference = adapter.analyze("英語")
    assert [item.code for item in preference.supported] == ["en"]
    assert session.calls == []
    assert len(session.gemini_calls) == 1


def test_enrollment_gemini_failure_still_raises(monkeypatch):
    monkeypatch.setattr("src.infra.language_pref_client.time.sleep", lambda *_args, **_kwargs: None)
    session = HybridSession(status=401, gemini_status=500)
    with pytest.raises(Exception):
        _adapter(session).analyze("英語")
    assert len(session.gemini_calls) == 2
