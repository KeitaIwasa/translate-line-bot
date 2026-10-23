"""言語を書き換える前の決定。追加・削除・置換・不明を、グループの状態ごとに尽くす。

未知の組み合わせは保存しない。削除を置換として扱わない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .language_mention import (
    catalog_labels,
    completion_prompt,
    enrollment_prompt,
    exceeds_language_limit,
    language_label,
    language_not_in_group_prompt,
    need_both_languages_prompt,
    no_languages_yet_prompt,
    over_limit_notice,
    project_language_codes,
    selection_prompt,
    swap_clarify_prompt,
    unique_language_codes,
    unrecognized_prompt,
    unsupported_prompt,
)

_MODES = frozenset({"add", "remove", "replace", "add_remove", "swap"})
_STATES = frozenset({"empty", "some", "at_limit"})


@dataclass(frozen=True)
class LanguageUpdate:
    """save 以外は言語行を書き換えない。enable_translation が None ならフラグも触らない。"""

    action: str  # save | reply | noop | reset | show
    mode: str  # add | remove | replace | add_remove | unknown | reset | show
    state: str  # empty | some | at_limit
    write: str = "none"  # none | replace | add | remove | apply
    languages: tuple[tuple[str, str], ...] = ()
    added: tuple[tuple[str, str], ...] = ()
    removed: tuple[str, ...] = ()
    reply: str = ""
    page_error: str | None = None
    enable_translation: bool | None = None
    changed: bool = False


def normalize_language_mode(mode: str) -> str:
    """メンションの set は置換。それ以外の未知語は unknown。"""

    if mode == "set":
        return "replace"
    if mode in _MODES:
        return mode
    return "unknown"


def language_group_state(current: Sequence[str], *, limit: int) -> str:
    count = len(unique_language_codes(current))
    if count <= 0:
        return "empty"
    resolved = limit if isinstance(limit, int) and limit >= 1 else 1
    if count >= resolved:
        return "at_limit"
    return "some"


def decide_language_update(
    *,
    mode: str,
    current: Sequence[tuple[str, str]] = (),
    requested: Sequence[tuple[str, str]] = (),
    remove: Sequence[str] = (),
    limit: int,
    family: str,
    kind: str = "languages",
    unsupported: Sequence[str] = (),
    swap_from: str = "",
    swap_to: str = "",
) -> LanguageUpdate:
    """初回設定、既存グループ、ウェブの置換が同じ表を使う。

    利用者が起こした言語操作は、保存しても保存しなくても返信を持つ。
    """

    update = _decide_language_update(
        mode=mode,
        current=current,
        requested=requested,
        remove=remove,
        limit=limit,
        family=family,
        kind=kind,
        unsupported=unsupported,
        swap_from=swap_from,
        swap_to=swap_to,
    )
    if not (update.reply or "").strip():
        raise ValueError("language update has no user-facing reply")
    return update


def _decide_language_update(
    *,
    mode: str,
    current: Sequence[tuple[str, str]],
    requested: Sequence[tuple[str, str]],
    remove: Sequence[str],
    limit: int,
    family: str,
    kind: str,
    unsupported: Sequence[str],
    swap_from: str,
    swap_to: str,
) -> LanguageUpdate:
    canonical = normalize_language_mode(mode)
    current_pairs = _pairs(current)
    requested_pairs = _pairs(requested)
    current_codes = unique_language_codes([code for code, _name in current_pairs])
    state = language_group_state(current_codes, limit=limit)
    if state not in _STATES:
        raise ValueError(f"unknown language state: {state}")
    names = _names(current_pairs, requested_pairs)

    if kind == "nonlinguistic":
        return _reply(canonical, state, unrecognized_prompt(family), page_error="unrecognized")
    if kind == "translate_all":
        return _reply(
            canonical,
            state,
            selection_prompt(catalog_labels(family), family=family, limit=limit),
            page_error="select",
        )
    if kind == "unsupported":
        return _reply(canonical, state, unsupported_prompt(unsupported, family), page_error="unsupported")
    if kind == "swap_unclear" or canonical == "swap":
        return _decide_swap(
            state=state,
            current_codes=current_codes,
            names=names,
            limit=limit,
            family=family,
            kind=kind,
            swap_from=swap_from,
            swap_to=swap_to,
        )
    if kind != "languages":
        return _reply(canonical, state, unrecognized_prompt(family), page_error="unrecognized")
    if canonical == "unknown":
        return _reply("unknown", state, unrecognized_prompt(family), page_error="unrecognized")
    if canonical not in _MODES:
        raise ValueError(f"unknown language mode: {canonical}")

    # 言語が無いグループへの削除は、その言語を初回設定として保存しない
    if canonical == "remove" and state == "empty":
        return _reply(
            "remove",
            "empty",
            no_languages_yet_prompt(family),
            page_error="none_set",
            enable_translation=False,
        )

    if canonical == "remove":
        missing = _remove_missing(current_codes, requested_pairs)
        if missing is not None:
            if not missing:
                return _reply("remove", state, unrecognized_prompt(family), page_error="unrecognized")
            return _reply(
                "remove",
                state,
                language_not_in_group_prompt(
                    _pair_labels(missing, family),
                    _code_labels(current_codes, names, family),
                    family=family,
                ),
                page_error="not_set",
            )

    # 追加と削除の役割が今の設定と逆でも、入れ替えて適用しない
    if canonical == "add_remove" and _replace_roles_contradict(current_codes, requested_pairs, remove):
        return _reply(
            "add_remove",
            state,
            swap_clarify_prompt(_code_labels(current_codes, names, family), family=family),
            page_error="clarify",
        )

    if canonical == "replace" and not requested_pairs:
        return _reply(canonical, state, enrollment_prompt(family), page_error="empty")

    projected = _project(canonical, current_codes, requested_pairs, remove)
    result = tuple((code, names.get(code, "")) for code in projected)
    # 順序だけ違う同じ集合は変更ではない。削除を置換へ落とさない
    same = set(projected) == set(current_codes)

    if exceeds_language_limit(projected, limit=limit):
        if canonical == "remove":
            raise ValueError("remove cannot exceed the language limit")
        notice_mode = "replace" if canonical == "replace" else "add"
        notice_current = [] if canonical == "replace" else current_codes
        notice = over_limit_notice(
            family=family,
            limit=limit,
            current=notice_current,
            requested=requested_pairs,
            mode=notice_mode,
            remove=remove if canonical == "add_remove" else (),
        )
        if not notice:
            raise ValueError("over-limit update produced no reply")
        return _reply(canonical, state, notice, page_error="too_many")

    if len(projected) < 2:
        labels = [language_label(code, family, stored=names.get(code, "")) for code in projected]
        if not labels:
            labels = [
                language_label(code, family, surface=surface)
                for code, surface in requested_pairs
                if code
            ]
        return _reply(
            canonical,
            state,
            need_both_languages_prompt(labels, family=family),
            page_error="need_pair",
        )

    added = tuple(pair for pair in result if pair[0] not in set(current_codes))
    removed = tuple(code for code in current_codes if code not in set(projected))
    write = _write_for(canonical, same)
    return LanguageUpdate(
        action="save",
        mode=canonical,
        state=state,
        write=write,
        languages=result,
        added=added,
        removed=removed,
        reply=completion_prompt(result, family=family),
        enable_translation=True,
        changed=not same,
    )


def _decide_swap(
    *,
    state: str,
    current_codes: Sequence[str],
    names: dict[str, str],
    limit: int,
    family: str,
    kind: str,
    swap_from: str,
    swap_to: str,
) -> LanguageUpdate:
    """外す言語と入れる言語が決まっているときだけ保存する。逆向きには直さない。"""

    current_labels = _code_labels(current_codes, names, family)
    clarify = _reply("swap", state, swap_clarify_prompt(current_labels, family=family), page_error="clarify")
    source = (swap_from or "").strip().lower()
    target = (swap_to or "").strip().lower()
    current_set = set(current_codes)
    if kind == "swap_unclear" or not source or not target or source == target:
        return clarify
    # 外す側が未設定、または入れる側が設定済みなら、役割を入れ替えない
    if source not in current_set or target in current_set:
        return clarify

    projected = [code for code in current_codes if code != source]
    projected.append(target)
    projected = unique_language_codes(projected)
    result = tuple((code, names.get(code, "")) for code in projected)
    if exceeds_language_limit(projected, limit=limit):
        notice = over_limit_notice(
            family=family,
            limit=limit,
            current=[],
            requested=result,
            mode="replace",
        )
        if not notice:
            raise ValueError("over-limit update produced no reply")
        return _reply("swap", state, notice, page_error="too_many")
    if len(projected) < 2:
        return _reply(
            "swap",
            state,
            need_both_languages_prompt(_code_labels(projected, names, family), family=family),
            page_error="need_pair",
        )
    added = tuple(pair for pair in result if pair[0] not in current_set)
    removed = tuple(code for code in current_codes if code not in set(projected))
    return LanguageUpdate(
        action="save",
        mode="swap",
        state=state,
        write="replace",
        languages=result,
        added=added,
        removed=removed,
        reply=completion_prompt(result, family=family),
        enable_translation=True,
        changed=True,
    )


def _remove_missing(
    current_codes: Sequence[str],
    requested_pairs: Sequence[tuple[str, str]],
) -> list[tuple[str, str]] | None:
    """外す言語が一つも今の設定に無いとき、その言語の組を返す。空なら聞き返す。"""

    if not requested_pairs:
        return []
    current_set = set(current_codes)
    if any(code in current_set for code, _name in requested_pairs):
        return None
    return list(requested_pairs)


def _replace_roles_contradict(
    current_codes: Sequence[str],
    requested_pairs: Sequence[tuple[str, str]],
    remove: Sequence[str],
) -> bool:
    """削除が全部未設定で、追加が全部設定済みなら、向きが逆。入れ替えない。"""

    add_codes = [code for code, _name in requested_pairs]
    remove_codes = unique_language_codes(remove)
    if not add_codes or not remove_codes:
        return False
    current_set = set(current_codes)
    if any(code in current_set for code in remove_codes):
        return False
    if any(code not in current_set for code in add_codes):
        return False
    return True


def _code_labels(codes: Sequence[str], names: dict[str, str], family: str) -> list[str]:
    return [language_label(code, family, stored=names.get(code, "")) for code in codes if code]


def _pair_labels(pairs: Sequence[tuple[str, str]], family: str) -> list[str]:
    return [language_label(code, family, surface=surface) for code, surface in pairs if code]


def _project(mode: str, current: Sequence[str], requested: Sequence[tuple[str, str]], remove: Sequence[str]) -> list[str]:
    incoming = [code for code, _name in requested]
    if mode == "replace":
        return project_language_codes([], incoming, mode="replace")
    if mode == "add":
        return project_language_codes(current, incoming, mode="add")
    if mode == "remove":
        return project_language_codes(current, [], mode="remove", remove=incoming)
    if mode == "add_remove":
        return project_language_codes(current, incoming, mode="add", remove=remove)
    raise ValueError(f"unknown language mode: {mode}")


def _write_for(mode: str, same: bool) -> str:
    if same and mode != "replace":
        return "none"
    if mode == "replace":
        return "replace"
    if mode == "add":
        return "add"
    if mode == "remove":
        return "remove"
    if mode == "add_remove":
        return "apply"
    raise ValueError(f"unknown language mode: {mode}")


def _reply(
    mode: str,
    state: str,
    reply: str,
    *,
    page_error: str | None = None,
    enable_translation: bool | None = None,
) -> LanguageUpdate:
    if not (reply or "").strip():
        raise ValueError("language update reply is empty")
    return LanguageUpdate(
        action="reply",
        mode=mode,
        state=state,
        reply=reply,
        page_error=page_error,
        enable_translation=enable_translation,
    )


def _noop(mode: str, state: str) -> LanguageUpdate:
    return LanguageUpdate(action="noop", mode=mode, state=state)


def _pairs(items: Sequence) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            code, name = item, ""
        else:
            code = item[0] if item else ""
            name = item[1] if len(item) > 1 and isinstance(item[1], str) else ""
        lowered = (code or "").strip().lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        pairs.append((lowered, name or ""))
    return pairs


def _names(*groups: Sequence[tuple[str, str]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for group in groups:
        for code, name in group:
            if code and code not in names and name:
                names[code] = name
            elif code and code not in names:
                names[code] = ""
    return names
