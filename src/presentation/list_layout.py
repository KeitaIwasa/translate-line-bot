"""メンション返信の番号付き・箇条書きを1項目1行に整える。"""

from __future__ import annotations

import re

# 全角・アラビア・ペルシア数字を ASCII に寄せて、連番判定に使う
_DIGIT_TRANSLATION = str.maketrans(
    {
        **{chr(ord("０") + index): str(index) for index in range(10)},
        **{chr(ord("٠") + index): str(index) for index in range(10)},
        **{chr(ord("۰") + index): str(index) for index in range(10)},
    }
)

# LINE の双方向制御文字。行頭判定では無視する
_FORMAT_CHARS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff"

# 半角空白・タブ・和文の全角空白。改行は含めない
_HSPACE = " \t\u3000"

_NUMBERED_RE = re.compile(
    # 小数や URL の途中は除外しつつ、文末のピリオドの直後にある次の番号は拾う
    rf"(?:^|[{_HSPACE}]+|(?<=[。！？!?：:]))"
    r"(?<![0-9０-９٠-٩۰-۹.．/])"
    r"(?P<num>[0-9０-９٠-٩۰-۹]{1,2})"
    r"[.．。)）、,，]"
    # 英語の "1. Add" も、空白なしの "１．友だち" も項目の開始として扱う
    rf"[{_HSPACE}]*"
    r"(?![0-9０-９٠-٩۰-۹])",
    re.MULTILINE,
)

_BULLET_RE = re.compile(
    rf"(?:^|[{_HSPACE}]+)"
    r"(?P<mark>[-•・*])"
    rf"[{_HSPACE}]+"
    r"(?![0-9０-９٠-٩۰-۹])",
    re.MULTILINE,
)

_CLAUSE_ENDINGS = set("。！？!?：:.)）」』")


def normalize_inline_lists(text: str) -> str:
    """同一行に並んだ番号・箇条書きを、項目ごとの行へ分ける。

    すでに1項目1行の文字列、URL、小数、バージョン番号は変えない。
    """

    if not text:
        return text
    numbered = _split_numbered_lists(text)
    return _split_bullet_lists(numbered)


def _split_numbered_lists(text: str) -> str:
    matches = list(_NUMBERED_RE.finditer(text))
    if len(matches) < 1:
        return text

    spans: list[tuple[int, int]] = []
    for run in _consecutive_runs(text, matches):
        first = run[0]
        marker_at = first.start("num")
        at_line_start = _is_line_start(text, marker_at)
        number = _number_value(first.group("num"))
        if len(run) == 1:
            # 「手順: 1. ...」のように、導入の直後にある単独項目だけ改行する
            if at_line_start or number != 1 or not _ends_with_colon(_text_before(text, marker_at)):
                continue
            span = _break_span(text, marker_at)
            if span:
                spans.append(span)
            continue
        if not at_line_start and number != 1:
            continue
        if not at_line_start and not _ends_with_clause(_text_before(text, marker_at)):
            continue
        for match in run:
            span = _break_span(text, match.start("num"))
            if span:
                spans.append(span)
    return _apply_spans(text, spans)


def _split_bullet_lists(text: str) -> str:
    matches = list(_BULLET_RE.finditer(text))
    if not matches:
        return text

    spans: list[tuple[int, int]] = []
    for run in _same_line_runs(text, matches):
        if len(run) == 1:
            marker_at = run[0].start("mark")
            if _is_line_start(text, marker_at) or not _ends_with_colon(_text_before(text, marker_at)):
                continue
            span = _break_span(text, marker_at)
            if span:
                spans.append(span)
            continue
        for match in run:
            span = _break_span(text, match.start("mark"))
            if span:
                spans.append(span)
    return _apply_spans(text, spans)


def _consecutive_runs(text: str, matches: list[re.Match[str]]) -> list[list[re.Match[str]]]:
    runs: list[list[re.Match[str]]] = []
    current: list[re.Match[str]] = []
    for match in matches:
        if not current:
            current = [match]
            continue
        previous = current[-1]
        between = text[previous.end() : match.start()]
        previous_number = _number_value(previous.group("num"))
        number = _number_value(match.group("num"))
        if "\n" not in between and number == previous_number + 1:
            current.append(match)
            continue
        runs.append(current)
        current = [match]
    if current:
        runs.append(current)
    return runs


def _same_line_runs(text: str, matches: list[re.Match[str]]) -> list[list[re.Match[str]]]:
    runs: list[list[re.Match[str]]] = []
    current: list[re.Match[str]] = []
    for match in matches:
        if not current:
            current = [match]
            continue
        between = text[current[-1].end() : match.start()]
        if "\n" not in between:
            current.append(match)
            continue
        runs.append(current)
        current = [match]
    if current:
        runs.append(current)
    return runs


def _number_value(raw: str) -> int:
    return int(raw.translate(_DIGIT_TRANSLATION))


def _text_before(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    before = text[line_start:index]
    for char in _FORMAT_CHARS:
        before = before.replace(char, "")
    return before.rstrip(_HSPACE)


def _is_line_start(text: str, index: int) -> bool:
    return _text_before(text, index) == ""


def _ends_with_clause(before: str) -> bool:
    return bool(before) and before[-1] in _CLAUSE_ENDINGS


def _ends_with_colon(before: str) -> bool:
    return bool(before) and before[-1] in ":："


def _break_span(text: str, marker_index: int) -> tuple[int, int] | None:
    """マーカー直前の空白を改行に置き換える範囲。行頭なら何もしない。"""

    if _is_line_start(text, marker_index):
        return None
    start = marker_index
    while start > 0 and text[start - 1] in _HSPACE:
        start -= 1
    return (start, marker_index)


def _apply_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(text[cursor:start])
        parts.append("\n")
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)
