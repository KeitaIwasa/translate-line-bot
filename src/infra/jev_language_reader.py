from __future__ import annotations

import logging
import math
import re
import time
from typing import Callable, Optional

import requests

from ..domain.models import CommandDecision, LanguageChoice, LanguagePreference
from .jev_api import extract_current_languages, extract_user_message, post_systemone, resolve_api_key

logger = logging.getLogger(__name__)

# Jev は自由文を生成できないため、翻訳でよく頼まれる言語だけを yes/no で読む。
# ここに無い言語は outside_catalog が閾値以上、または不確実帯なら Gemini へ戻す。
LANGUAGE_CATALOG: tuple[tuple[str, str], ...] = (
    ("ja", "Japanese"),
    ("en", "English"),
    ("zh", "Chinese"),
    ("ko", "Korean"),
    ("th", "Thai"),
    ("vi", "Vietnamese"),
    ("id", "Indonesian"),
    ("ms", "Malay"),
    ("tl", "Filipino"),
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("ru", "Russian"),
    ("ar", "Arabic"),
    ("hi", "Hindi"),
    ("bn", "Bengali"),
    ("ta", "Tamil"),
    ("ur", "Urdu"),
    ("fa", "Persian"),
    ("tr", "Turkish"),
    ("pl", "Polish"),
    ("nl", "Dutch"),
    ("sv", "Swedish"),
    ("da", "Danish"),
    ("fi", "Finnish"),
    ("no", "Norwegian"),
    ("uk", "Ukrainian"),
    ("cs", "Czech"),
    ("ro", "Romanian"),
    ("hu", "Hungarian"),
    ("el", "Greek"),
    ("he", "Hebrew"),
    ("my", "Burmese"),
    ("km", "Khmer"),
    ("lo", "Lao"),
    ("ne", "Nepali"),
    ("si", "Sinhala"),
    ("sw", "Swahili"),
)

LANGUAGE_NAMES = dict(LANGUAGE_CATALOG)
_CATALOG_CODES = [code for code, _ in LANGUAGE_CATALOG]
_CATALOG_NAME_LIST = ", ".join(name for _, name in LANGUAGE_CATALOG)
_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$")
_SETTINGS_OPERATIONS = ("show", "reset_all", "add", "remove", "add_and_remove")
# 初期設定で全 want_* が閾値未満のとき、この値を超えるスコアは「言語が無い」と言い切れない
_CLEARLY_NEGATIVE_NOUL = 0.2
# add_* / remove_* / want_* / outside_catalog がこの値以上かつ閾値未満なら不確実帯。
# 一つでも残れば、他の言語が閾値以上でも部分採用せず Gemini に戻す。
_UNCERTAIN_NOUL_FLOOR = 0.5

# 確認文の原文は英語。Jev は文を生成できないので、よく使う言語だけ静的に持つ。
# 静的訳が無いカタログ言語は空文字を返し、ハンドラーの翻訳経路に渡す。
LANGUAGE_SETTINGS_ACK_BASE = "Language settings have been updated."
_ACK_BY_PREFIX = (
    ("ja", "言語設定を更新しました。"),
    ("zh", "已更新语言设置。"),
    ("ko", "언어 설정을 업데이트했습니다."),
    ("th", "อัปเดตการตั้งค่าภาษาแล้ว"),
)


class _InvalidAnswers(Exception):
    """Jev の answers が質問と揃っていない。"""


def confirmation_ack(instruction_language: str) -> str:
    """言語設定の短い確認文。

    英語と、静的訳がある言語だけ文を返す。書き言葉が未確定のときと、
    静的訳が無い言語は空文字にし、ハンドラーが英語原文を指示言語へ翻訳する。
    """
    code = (instruction_language or "").lower().replace("_", "-")
    # 空のまま英語文を確定すると、後段が本文から言語を直しても ack は作り直されない
    if not code:
        return ""
    if code.startswith("en"):
        return LANGUAGE_SETTINGS_ACK_BASE
    for prefix, text in _ACK_BY_PREFIX:
        if code.startswith(prefix):
            return text
    return ""


def read_language_settings(
    *,
    session: requests.Session,
    api_key_provider: Callable[[], str],
    model: str,
    timeout_seconds: float,
    min_confidence: float,
    router_input: str,
) -> tuple[Optional[CommandDecision], str]:
    """言語設定の operation と言語リストを Jev で読む。

    戻り値の reason が "ok" のときだけ decision を使う。それ以外は Gemini へ戻す。
    """
    started = time.perf_counter()
    try:
        decision, reason = _read_language_settings(
            session=session,
            api_key_provider=api_key_provider,
            model=model,
            timeout_seconds=timeout_seconds,
            min_confidence=min_confidence,
            router_input=router_input,
        )
    except Exception:
        logger.warning("Jev language settings reader failed", exc_info=True)
        decision, reason = None, "reader_error"
    logger.info(
        "Jev language settings | reason=%s elapsed_ms=%.0f",
        reason,
        (time.perf_counter() - started) * 1000,
    )
    return decision, reason


def read_language_preference(
    *,
    session: requests.Session,
    api_key_provider: Callable[[], str],
    model: str,
    timeout_seconds: float,
    min_confidence: float,
    text: str,
) -> tuple[Optional[LanguagePreference], str]:
    """登録メッセージから希望言語を Jev で読む。

    reason "ok" は preference を使う。
    "no_languages" は全 want_* がはっきり否定されたときだけで、None のまま返す。
    want_* のいずれかが不確実帯 [0.5, 閾値) なら、他言語が閾値以上でも
    "low_confidence" とし、依頼全体を Gemini に戻す。
    閾値以上のカタログ外は "outside_catalog"。
    """
    started = time.perf_counter()
    try:
        preference, reason = _read_language_preference(
            session=session,
            api_key_provider=api_key_provider,
            model=model,
            timeout_seconds=timeout_seconds,
            min_confidence=min_confidence,
            text=text,
        )
    except Exception:
        logger.warning("Jev language preference reader failed", exc_info=True)
        preference, reason = None, "reader_error"
    logger.info(
        "Jev language preference | reason=%s elapsed_ms=%.0f",
        reason,
        (time.perf_counter() - started) * 1000,
    )
    return preference, reason


def _read_language_settings(
    *,
    session: requests.Session,
    api_key_provider: Callable[[], str],
    model: str,
    timeout_seconds: float,
    min_confidence: float,
    router_input: str,
) -> tuple[Optional[CommandDecision], str]:
    message = extract_user_message(router_input)
    raw_current = extract_current_languages(router_input)
    # 質問IDにできないコードは state に残し、remove の質問にはしない
    current = [code for code in raw_current if _CODE_RE.match(code)]
    questions = _settings_questions(current)
    body, reason = post_systemone(
        session,
        api_key=resolve_api_key(api_key_provider),
        model=model,
        state={"message": message, "current_languages": raw_current},
        questions=questions,
        timeout=timeout_seconds,
    )
    if body is None:
        return None, reason
    try:
        answers = body["answers"]
        operation, operation_confidence = _choice(answers, "operation")
        instruction_choice, instruction_confidence = _choice(answers, "instruction_language")
        outside = _noul(answers, "outside_catalog")
        explicit_reset = _noul(answers, "explicit_reset")
        add_scored = _code_scores(answers, "add_", _CATALOG_CODES)
        remove_scored = _code_scores(answers, "remove_", current)
    except _InvalidAnswers:
        return None, "invalid_response"

    # 操作の確度は「しない」で確定できない。不確実帯より下でも閾値未満は low_confidence
    if operation_confidence < min_confidence:
        return None, "low_confidence"
    if operation not in _SETTINGS_OPERATIONS:
        return None, "invalid_choice"
    # 0.79 のように閾値直下でも、カタログ外を落とさず Gemini に任せる
    outside_reason = _outside_fallback_reason(outside, min_confidence)
    if outside_reason is not None:
        return None, outside_reason
    # 英語 0.99 とタイ語 0.79 のように、確定言語があっても不確実帯が残れば全体を戻す
    uncertain = _uncertain_language_reason(
        [score for _, score in add_scored] + [score for _, score in remove_scored],
        min_confidence,
    )
    if uncertain is not None:
        return None, uncertain

    add_codes = _codes_at_or_above(add_scored, min_confidence)
    remove_codes = _codes_at_or_above(remove_scored, min_confidence)

    instruction = ""
    if instruction_choice in LANGUAGE_NAMES and instruction_confidence >= min_confidence:
        instruction = instruction_choice

    if operation == "reset_all":
        # 言語名がはっきり付いているリセットは、操作の取り違えかもしれないので Gemini に任せる
        if add_codes or remove_codes:
            return None, "reset_conflict"
        # 明示的なリセットでない「言語を聞いただけ」は設定を変えない
        if explicit_reset < min_confidence:
            return _settings_decision("show", [], [], instruction), "ok"
        return _settings_decision("reset_all", [], [], instruction), "ok"
    if operation == "show":
        if add_codes or remove_codes or explicit_reset >= min_confidence:
            return None, "mixed_signals"
        return _settings_decision("show", [], [], instruction), "ok"
    # 「設定をリセットして英語だけにして」のように、明示リセットと追加・削除が同時なら
    # 片方だけ適用せず Gemini に戻す
    if operation in {"add", "remove", "add_and_remove"} and explicit_reset >= min_confidence:
        return None, "reset_conflict"
    if operation == "add" and remove_codes:
        return None, "mixed_signals"
    if operation == "remove" and add_codes:
        return None, "mixed_signals"

    if operation == "add":
        chosen_add, chosen_remove = add_codes, []
    elif operation == "remove":
        chosen_add, chosen_remove = [], remove_codes
    else:
        # add_and_remove は置換なので、追加と削除の両方が閾値を超えないと確定できない。
        # 「英語だけにして」で remove が空のまま add だけ成功すると、残した言語を消したと誤って報告する。
        if not add_codes or not remove_codes:
            return None, "mixed_signals"
        # 同じコードが両方にあると、ハンドラーは消した直後に入れ直して成功してしまう
        if set(add_codes) & set(remove_codes):
            return None, "mixed_signals"
        chosen_add, chosen_remove = add_codes, remove_codes
    if not chosen_add and not chosen_remove:
        return None, "no_languages"
    return _settings_decision(operation, chosen_add, chosen_remove, instruction), "ok"


def _read_language_preference(
    *,
    session: requests.Session,
    api_key_provider: Callable[[], str],
    model: str,
    timeout_seconds: float,
    min_confidence: float,
    text: str,
) -> tuple[Optional[LanguagePreference], str]:
    body, reason = post_systemone(
        session,
        api_key=resolve_api_key(api_key_provider),
        model=model,
        state={"message": text},
        questions=_enrollment_questions(),
        timeout=timeout_seconds,
    )
    if body is None:
        return None, reason
    try:
        answers = body["answers"]
        primary, primary_confidence = _choice(answers, "primary_language")
        outside = _noul(answers, "outside_catalog")
        want_scores = [_noul(answers, f"want_{code}") for code in _CATALOG_CODES]
    except _InvalidAnswers:
        return None, "invalid_response"

    wanted = [code for code, score in zip(_CATALOG_CODES, want_scores) if score >= min_confidence]
    # メンション後と同じく、不確実なカタログ外は初期設定でも Gemini に任せる
    outside_reason = _outside_fallback_reason(outside, min_confidence)
    if outside_reason is not None:
        return None, outside_reason
    # 確認UIの言語が曖昧、またはカタログ外の書き言葉なら Gemini に任せる
    if primary not in LANGUAGE_NAMES or primary_confidence < min_confidence:
        return None, "low_confidence"
    # 英語 0.99 とタイ語 0.79 のように、確定言語があっても不確実帯が残れば全体を戻す
    uncertain = _uncertain_language_reason(want_scores, min_confidence)
    if uncertain is not None:
        return None, uncertain
    if not wanted:
        # 不確実帯未満でも 0.2 を超えるスコアは、言語なしと断定せず Gemini に任せる
        if all(score <= _CLEARLY_NEGATIVE_NOUL for score in want_scores):
            return None, "no_languages"
        return None, "low_confidence"
    return (
        LanguagePreference(
            supported=[_choice_for(code) for code in wanted],
            unsupported=[],
            confirm_label="OK",
            cancel_label="Cancel",
            primary_language=primary,
        ),
        "ok",
    )


def _settings_decision(
    operation: str,
    add_codes: list[str],
    remove_codes: list[str],
    instruction: str,
) -> CommandDecision:
    return CommandDecision(
        action="language_settings",
        operation=operation,
        languages_to_add=[_choice_for(code) for code in add_codes],
        languages_to_remove=[_choice_for(code) for code in remove_codes],
        instruction_language=instruction,
        ack_text="" if operation == "show" else confirmation_ack(instruction),
        # reset_all を返すのは explicit_reset が閾値を超えたときだけ。
        # ハンドラーは英語・日本語以外の全消去を、このフラグが無いと show に落とす。
        explicit_reset=operation == "reset_all",
    )


def _in_uncertain_band(score: float, min_confidence: float) -> bool:
    """[0.5, 閾値) は肯定とも否定とも言い切れない。

    閾値が 0.5 以下のときは帯が空になり、閾値以上の判定だけが残る。
    """
    return _UNCERTAIN_NOUL_FLOOR <= score < min_confidence


def _uncertain_language_reason(scores: list[float], min_confidence: float) -> Optional[str]:
    """add_* / remove_* / want_* の不確実帯が一つでもあれば low_confidence。

    Jev の結果を使うのは、各スコアが閾値以上か 0.5 未満のときだけ。
    0.5 未満ははっきりした否定として落とし、閾値以上だけを採用する。
    """
    if any(_in_uncertain_band(score, min_confidence) for score in scores):
        return "low_confidence"
    return None


def _outside_fallback_reason(outside: float, min_confidence: float) -> Optional[str]:
    """カタログ外スコアが閾値以上、または不確実帯なら Gemini へ戻す理由。

    0.5 未満ははっきりした否定として Jev の結果を使う。
    閾値そのものが 0.5 より低くても、閾値以上はカタログ外として戻す。
    """
    if outside >= min_confidence:
        return "outside_catalog"
    if _in_uncertain_band(outside, min_confidence):
        return "low_confidence"
    return None


def _choice_for(code: str) -> LanguageChoice:
    return LanguageChoice(code=code, name=LANGUAGE_NAMES.get(code, code))


def _settings_questions(current_languages: list[str]) -> dict:
    questions = {
        "operation": {
            "type": "choice",
            "instructions": (
                "The user mentioned a translation bot. `message` is what they said. "
                "`current_languages` are the language codes already configured. "
                "Which language-setting operation do they want?"
            ),
            "criteria": {
                "show": (
                    "Ask which translation languages are currently set, without adding, "
                    "removing, or resetting them."
                ),
                "reset_all": (
                    "They explicitly ask to clear, reset, or set the languages again, "
                    "and they do not name languages to keep or add."
                ),
                "add": "Add one or more languages and do not remove any.",
                "remove": "Remove one or more languages and do not add any.",
                "add_and_remove": "Replace the set, or both add some languages and remove others.",
            },
        },
        "explicit_reset": {
            "type": "noul",
            "instructions": (
                "Did the user explicitly ask to clear, reset, delete, or set the translation "
                "languages again from scratch? "
                "Answer no when they ask which languages are currently set, "
                "and no when they only add or remove named languages."
            ),
            "criteria": {
                "true": "They explicitly asked to reset or set the languages again",
                "false": "They did not explicitly ask to reset",
            },
        },
        "instruction_language": _message_language_question(),
        "outside_catalog": _outside_question(),
    }
    for code in _CATALOG_CODES:
        name = LANGUAGE_NAMES[code]
        questions[f"add_{code}"] = {
            "type": "noul",
            "instructions": (
                f"Does the user want the bot to translate into {name} ({code}), "
                "or to add or keep that language? "
                "Answer no if they only wrote the message in that language, or if they want it removed."
            ),
            "criteria": {
                "true": f"The user wants {name} added or kept",
                "false": f"The user does not want {name} added or kept",
            },
        }
    for code in current_languages:
        name = LANGUAGE_NAMES.get(code, code)
        questions[f"remove_{code}"] = {
            "type": "noul",
            "instructions": (
                f"Does the user want to stop translating into {name} ({code}) "
                "or remove it from `current_languages`?"
            ),
            "criteria": {
                "true": f"The user wants {name} removed",
                "false": f"The user does not want {name} removed",
            },
        }
    return questions


def _enrollment_questions() -> dict:
    questions = {
        "primary_language": _message_language_question(),
        "outside_catalog": _outside_question(),
    }
    for code in _CATALOG_CODES:
        name = LANGUAGE_NAMES[code]
        questions[f"want_{code}"] = {
            "type": "noul",
            "instructions": (
                f"Does the user want {name} ({code}) enabled for translation? "
                "This message is a language preference, so count a language they name "
                "even if the message is written in it."
            ),
            "criteria": {
                "true": f"The user asked for {name}",
                "false": f"The user did not ask for {name}",
            },
        }
    return questions


def _message_language_question() -> dict:
    criteria = {code: name for code, name in LANGUAGE_CATALOG}
    criteria["other"] = "None of the listed languages, or it cannot be told"
    return {
        "type": "choice",
        "instructions": "Which language is `message` written in?",
        "criteria": criteria,
    }


def _outside_question() -> dict:
    return {
        "type": "noul",
        "instructions": (
            "Does the user ask for a translation language that is not in this list: "
            f"{_CATALOG_NAME_LIST}? "
            "Count a dialect, script, or regional variety that the list does not name "
            "(for example Traditional Chinese as distinct from Chinese, or Cantonese). "
            "Do not count a language only because the user wrote the message in it."
        ),
        "criteria": {
            "true": "They named a translation language outside the list",
            "false": "Every translation language they named is in the list, or they named none",
        },
    }


def _choice(answers: dict, key: str) -> tuple[str, float]:
    raw = answers.get(key)
    if not isinstance(raw, dict):
        raise _InvalidAnswers(key)
    choice = str(raw.get("choice") or "").strip().lower()
    return choice, _unit_score(raw.get("confidence"), key)


def _noul(answers: dict, key: str) -> float:
    raw = answers.get(key)
    if not isinstance(raw, dict) or "noul" not in raw:
        raise _InvalidAnswers(key)
    return _unit_score(raw["noul"], key)


def _unit_score(value: object, key: str) -> float:
    """有限な [0, 1] だけを信頼度・Noul として受け付ける。

    float("NaN") や inf は例外にならず、その後の < と >= がすべて偽になる。
    範囲外も同様に回答全体を無効にし、Gemini へ戻す。
    """
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise _InvalidAnswers(key) from exc
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        raise _InvalidAnswers(key)
    return score


def _code_scores(answers: dict, prefix: str, codes: list[str]) -> list[tuple[str, float]]:
    return [(code, _noul(answers, f"{prefix}{code}")) for code in codes]


def _codes_at_or_above(scored: list[tuple[str, float]], min_confidence: float) -> list[str]:
    return [code for code, score in scored if score >= min_confidence]
