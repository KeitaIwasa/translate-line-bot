"""言語更新の決定表。モードと状態の組み合わせで、削除が置換に落ちないことを見る。"""

import pytest

from src.domain.services.language_mention import no_languages_yet_prompt, project_language_codes
from src.domain.services.language_update import decide_language_update

EN = ("en", "English")
JA = ("ja", "Japanese")
ZH = ("zh", "Chinese")
KO = ("ko", "Korean")
LIMIT = 3

STATES = {
    "empty": (),
    "some": (EN, JA),
    "at_limit": (EN, JA, ZH),
}


def _case(mode, state, requested=(), kind="languages", unsupported=(), action="save", enable=True, page_error=None):
    return (mode, state, requested, kind, unsupported, action, enable, page_error)


# 追加・削除・置換・不明 × 言語なし・あり・上限。絵文字と数字、未対応言語も含む
CASES = [
    _case("add", "empty", (EN, JA)),
    _case("add", "some", (ZH,)),
    _case("add", "at_limit", (KO,), action="reply", enable=None, page_error="too_many"),
    _case("add", "empty", (EN, JA, ZH, KO), action="reply", enable=None, page_error="too_many"),
    _case("remove", "empty", (EN, JA), action="reply", enable=False, page_error="none_set"),
    _case("remove", "some", (JA,), action="reply", enable=None, page_error="need_pair"),
    _case("remove", "at_limit", (ZH,)),
    _case("replace", "empty", (EN, JA)),
    _case("replace", "some", (EN, ZH)),
    _case("replace", "at_limit", (EN, JA)),
    _case("replace", "empty", (EN, JA, ZH, KO), action="reply", enable=None, page_error="too_many"),
    _case("set", "empty", (EN, JA)),
    _case("unknown", "empty", (EN, JA), action="reply", enable=None, page_error="unrecognized"),
    _case("unknown", "some", (ZH,), action="reply", enable=None, page_error="unrecognized"),
    _case("unknown", "at_limit", (KO,), action="reply", enable=None, page_error="unrecognized"),
    _case("frobnicate", "empty", (EN, JA), action="reply", enable=None, page_error="unrecognized"),
    _case("", "some", (EN, JA), action="reply", enable=None, page_error="unrecognized"),
    _case("add", "empty", kind="nonlinguistic", action="reply", enable=None, page_error="unrecognized"),
    _case("remove", "some", kind="nonlinguistic", action="reply", enable=None, page_error="unrecognized"),
    _case("replace", "at_limit", kind="nonlinguistic", action="reply", enable=None, page_error="unrecognized"),
    _case("add", "empty", kind="unsupported", unsupported=("Klingon",), action="reply", enable=None, page_error="unsupported"),
    _case("remove", "some", kind="unsupported", unsupported=("12345",), action="reply", enable=None, page_error="unsupported"),
    _case("replace", "at_limit", kind="unsupported", unsupported=("😂",), action="reply", enable=None, page_error="unsupported"),
    _case("add", "empty", kind="translate_all", action="reply", enable=None, page_error="select"),
    _case("remove", "at_limit", kind="translate_all", action="reply", enable=None, page_error="select"),
]


@pytest.mark.parametrize(
    ("mode", "state", "requested", "kind", "unsupported", "action", "enable", "page_error"),
    CASES,
)
def test_mode_and_state_decide_without_falling_through_to_replace(
    mode, state, requested, kind, unsupported, action, enable, page_error
):
    update = decide_language_update(
        mode=mode,
        current=STATES[state],
        requested=requested,
        limit=LIMIT,
        family="en",
        kind=kind,
        unsupported=unsupported,
    )

    assert update.action == action
    assert update.state == state
    assert update.enable_translation is enable
    assert update.page_error == page_error
    if action != "save":
        assert update.languages == ()
        assert update.write == "none"
        assert update.changed is False
    else:
        assert update.enable_translation is True
        assert len(update.languages) >= 2
        assert len(update.languages) <= LIMIT
    assert update.reply.strip()
    assert update.action != "noop"


def test_remove_on_an_empty_group_does_not_keep_the_named_languages():
    update = decide_language_update(
        mode="remove",
        current=(),
        requested=(EN, JA),
        limit=LIMIT,
        family="ja",
    )

    assert update.action == "reply"
    assert update.reply == no_languages_yet_prompt("ja")
    assert update.languages == ()
    assert update.enable_translation is False
    assert "en" not in update.reply
    assert "ja" not in update.reply


def test_same_languages_in_another_order_are_not_a_change():
    update = decide_language_update(
        mode="replace",
        current=(JA, EN),
        requested=(EN, JA),
        limit=LIMIT,
        family="en",
    )

    assert update.action == "save"
    assert update.changed is False


def test_unknown_projection_mode_raises():
    with pytest.raises(ValueError):
        project_language_codes(["en", "ja"], ["ko"], mode="nope")


def test_remove_of_a_language_that_is_not_set_replies():
    update = decide_language_update(
        mode="remove",
        current=(EN, JA, ZH),
        requested=(("ko", "Korean"),),
        limit=LIMIT,
        family="en",
    )

    assert update.action == "reply"
    assert update.page_error == "not_set"
    assert update.enable_translation is None
    assert update.write == "none"
    assert update.languages == ()
    assert "isn't set" in update.reply
    assert "Korean" in update.reply
    assert "English" in update.reply


def test_every_mode_and_state_has_a_user_facing_reply():
    """追加・削除・置換・不明 × 未設定・設定済み・上限。どの結果も返信がある。"""

    modes = ("add", "remove", "replace", "unknown")
    kinds = ("languages", "nonlinguistic", "unsupported")
    for mode in modes:
        for state in STATES:
            for kind in kinds:
                update = decide_language_update(
                    mode=mode,
                    current=STATES[state],
                    requested=(EN, JA) if mode != "remove" else (KO,),
                    limit=LIMIT,
                    family="en",
                    kind=kind,
                    unsupported=("Klingon",) if kind == "unsupported" else (),
                )
                assert update.reply.strip(), (mode, state, kind)
                assert update.action != "noop"


def test_inverted_add_and_remove_asks_instead_of_swapping_roles():
    contradicted = decide_language_update(
        mode="add_remove",
        current=(EN, JA),
        requested=(EN,),
        remove=("zh",),
        limit=LIMIT,
        family="ja",
    )
    assert contradicted.action == "reply"
    assert contradicted.page_error == "clarify"
    assert contradicted.languages == ()
    assert "どちらを外して" in contradicted.reply

    valid = decide_language_update(
        mode="add_remove",
        current=(EN, JA),
        requested=(ZH,),
        remove=("en",),
        limit=LIMIT,
        family="en",
    )
    assert valid.action == "save"
    assert {code for code, _name in valid.languages} == {"ja", "zh"}
