from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

import requests

from ..domain.models import CommandDecision, LanguageChoice, LanguagePreference
from .jev_api import extract_current_languages, extract_user_message, post_systemone, resolve_api_key

logger = logging.getLogger(__name__)

# Jev は自由文を生成できないため、翻訳でよく頼まれる言語だけを yes/no で読む。
# ここに無い言語は outside_catalog が立ったとき Gemini へ戻す。
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

    英語と、静的訳がある言語だけ文を返す。それ以外は空文字にし、
    ハンドラーが英語原文を指示言語へ翻訳する。
    """
    code = (instruction_language or "").lower().replace("_", "-")
    if not code or code.startswith("en"):
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

    reason "ok" は preference を使う。"no_languages" は言語が無いので None のまま。
    それ以外は Gemini へ戻す。
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
        add_codes = _selected_codes(answers, "add_", _CATALOG_CODES, min_confidence)
        remove_codes = _selected_codes(answers, "remove_", current, min_confidence)
    except _InvalidAnswers:
        return None, "invalid_response"

    if operation_confidence < min_confidence:
        return None, "low_confidence"
    if operation not in _SETTINGS_OPERATIONS:
        return None, "invalid_choice"
    if outside >= min_confidence:
        return None, "outside_catalog"

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
    if operation == "add" and remove_codes:
        return None, "mixed_signals"
    if operation == "remove" and add_codes:
        return None, "mixed_signals"

    if operation == "add":
        chosen_add, chosen_remove = add_codes, []
    elif operation == "remove":
        chosen_add, chosen_remove = [], remove_codes
    else:
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
        wanted = _selected_codes(answers, "want_", _CATALOG_CODES, min_confidence)
    except _InvalidAnswers:
        return None, "invalid_response"

    if outside >= min_confidence:
        return None, "outside_catalog"
    # 確認UIの言語が曖昧、またはカタログ外の書き言葉なら Gemini に任せる
    if primary not in LANGUAGE_NAMES or primary_confidence < min_confidence:
        return None, "low_confidence"
    if not wanted:
        return None, "no_languages"
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
    )


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
    try:
        choice = str(raw.get("choice") or "").strip().lower()
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise _InvalidAnswers(key) from exc
    return choice, confidence


def _noul(answers: dict, key: str) -> float:
    raw = answers.get(key)
    if not isinstance(raw, dict) or "noul" not in raw:
        raise _InvalidAnswers(key)
    try:
        return float(raw["noul"])
    except (TypeError, ValueError) as exc:
        raise _InvalidAnswers(key) from exc


def _selected_codes(answers: dict, prefix: str, codes: list[str], min_confidence: float) -> list[str]:
    selected: list[str] = []
    for code in codes:
        if _noul(answers, f"{prefix}{code}") >= min_confidence:
            selected.append(code)
    return selected
