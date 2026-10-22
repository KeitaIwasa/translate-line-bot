"""グループの署名付きリンクから翻訳言語を表示・保存する。"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Dict, Optional, Sequence
from urllib.parse import parse_qs

from .domain.services.language_mention import web_settings_notice
from .domain.services.language_settings_service import (
    normalize_catalog_selection,
    resolve_group_language_limit,
)
from .domain.services.language_update import decide_language_update
from .infra.jev_language_reader import LANGUAGE_CATALOG, LANGUAGE_NAMES
from .infra.signed_token import TokenError, verify_token
from .presentation.group_language_page import (
    language_option_label,
    page_family,
    render_group_language_page,
    render_language_link_error,
)

logger = logging.getLogger(__name__)

GROUP_LANGUAGE_SCOPE = "group_languages"
# プラン案内のリンクでも同じグループとして開ける。請求用セッションは別物なので含めない。
_ACCEPTED_SCOPES = {GROUP_LANGUAGE_SCOPE, "checkout"}
_MAX_GROUP_ID_LENGTH = 128


def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    from .config import get_settings

    settings = get_settings()
    return handle_group_language_request(
        event,
        repo=_get_repo(settings),
        line=_get_line(settings),
        secret=settings.subscription_token_secret,
        translator=_get_translator(settings),
    )


def handle_group_language_request(
    event: Dict[str, Any],
    *,
    repo,
    line,
    secret: str,
    translator=None,
) -> Dict[str, Any]:
    method = _method(event)
    if method not in {"GET", "POST"}:
        return _html_response(405, render_language_link_error("unavailable"))
    if not secret:
        logger.warning("group language page unavailable: token secret is empty")
        return _html_response(503, render_language_link_error("unavailable"))

    form = _form_values(event) if method == "POST" else {}
    query = event.get("queryStringParameters") or {}
    token, mismatch = _token(query, form)
    if mismatch or not token:
        reason = "invalid" if mismatch else "missing"
        return _html_response(401, render_language_link_error(reason))

    try:
        payload = _verify_group_token(token, secret)
    except TokenError as exc:
        logger.warning("group language token rejected: %s", exc)
        return _html_response(401, render_language_link_error("invalid"))

    group_id = str(payload["group_id"])
    current = _current_records(repo, group_id)
    limit = resolve_group_language_limit(repo, group_id)
    requested_ui = str(query.get("ui") or query.get("lang") or "")
    family = page_family(requested_ui, [code for code, _name in current])
    options = _options(current, family)
    current_labels = [language_option_label(code, family, stored) for code, stored in current if code]

    if method == "GET":
        return _html_response(
            200,
            render_group_language_page(
                family=family,
                token=token,
                options=options,
                checked=[code for code, _name in current],
                current_labels=current_labels,
                limit=limit,
            ),
        )

    submitted = form.get("languages") or []
    selection = normalize_catalog_selection(
        submitted,
        limit=limit,
        already=[code for code, _name in current],
    )
    stored_names = {(code or "").lower(): name for code, name in current}
    languages = tuple(
        (code, LANGUAGE_NAMES.get(code) or stored_names.get(code) or name)
        for code, name in selection.languages
    )
    # チャットと同じ決定。未対応や上限超えは保存しない。同じ集合は書き換えない
    update = decide_language_update(
        mode="replace",
        current=current,
        requested=languages,
        limit=limit,
        family=family,
        kind="unsupported" if selection.error == "unsupported" else "languages",
        unsupported=selection.unsupported,
    )
    if selection.error or update.action != "save":
        error = selection.error or update.page_error or "empty"
        return _html_response(
            400,
            render_group_language_page(
                family=family,
                token=token,
                options=options,
                checked=[code for code, _name in selection.languages],
                current_labels=current_labels,
                limit=limit,
                error=error,
                unsupported=selection.unsupported,
            ),
        )
    # 同じ選択でも、確認を閉じて翻訳を戻す。言語行は変えない
    _commit_web_languages(repo, group_id, languages, replace=update.changed)
    if not update.changed:
        return _html_response(
            200,
            render_group_language_page(
                family=family,
                token=token,
                options=options,
                checked=[code for code, _name in languages],
                current_labels=current_labels,
                limit=limit,
                notice="already",
            ),
        )

    saved = _current_records(repo, group_id) or list(languages)
    _push_update(line, group_id, saved, translate=translator)
    # ui を指定していないときは、保存後のグループ言語で表示を決める
    if not requested_ui.strip():
        family = page_family("", [code for code, _stored in saved])
    saved_labels = [language_option_label(code, family, stored) for code, stored in saved if code]
    saved_options = _options(saved, family)
    return _html_response(
        200,
        render_group_language_page(
            family=family,
            token=token,
            options=saved_options,
            checked=[code for code, _name in saved],
            current_labels=saved_labels,
            limit=limit,
            notice="saved",
        ),
    )


def _verify_group_token(token: str, secret: str) -> Dict[str, Any]:
    """グループに紐づく署名だけを見る。メンバー個人の認証はしない。

    リンクを開けたグループの誰でも言語を変えられる。トークンは推測できない HMAC で、
    別グループの group_id には使えない。フォームの group_id や user_id は読まない。
    """

    payload = verify_token(token, secret=secret)
    scope = str(payload.get("scope") or "")
    if scope not in _ACCEPTED_SCOPES:
        raise TokenError("invalid token scope")
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise TokenError("token expired")
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id or len(group_id) > _MAX_GROUP_ID_LENGTH or any(ch.isspace() for ch in group_id):
        raise TokenError("missing group")
    return {"group_id": group_id, "scope": scope}


def _token(query: Dict[str, Any], form: Dict[str, list]) -> tuple[str, bool]:
    query_token = str(query.get("st") or "").strip()
    body_values = form.get("st") or []
    body_token = str(body_values[0] or "").strip() if body_values else ""
    if query_token and body_token and query_token != body_token:
        return "", True
    return body_token or query_token, False


def _current_records(repo, group_id: str) -> list[tuple[str, str]]:
    fetcher = getattr(repo, "fetch_group_language_records", None)
    rows: Sequence = []
    if fetcher:
        try:
            rows = fetcher(group_id) or []
        except Exception:
            logger.warning("Failed to read group language records", exc_info=True)
            rows = []
    else:
        try:
            rows = getattr(repo, "fetch_group_languages", lambda *_: [])(group_id) or []
        except Exception:
            logger.warning("Failed to read group languages", exc_info=True)
            rows = []

    records: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            code, name = row, ""
        else:
            code = row[0] if row else ""
            name = row[1] if row and len(row) > 1 and isinstance(row[1], str) else ""
        lowered = (code or "").strip().lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        records.append((lowered, name or ""))
    return records


def _options(current: Sequence[tuple[str, str]], family: str) -> list[tuple[str, str, str]]:
    current_map = {code: name for code, name in current}
    ordered = [code for code, _name in LANGUAGE_CATALOG]
    for code in current_map:
        if code not in LANGUAGE_NAMES and code not in ordered:
            ordered.append(code)
    options: list[tuple[str, str, str]] = []
    for code in ordered:
        stored = current_map.get(code, "")
        label = language_option_label(code, family, stored)
        en = language_option_label(code, "en", stored)
        ja = language_option_label(code, "ja", stored)
        search = " ".join((code, label, en, ja)).casefold()
        options.append((code, label, search))
    return options


def _commit_web_languages(repo, group_id: str, languages, *, replace: bool) -> None:
    """ウェブ保存の確定。変更の有無で翻訳オンと確認のクローズが分かれないようにする。"""

    finalizer = getattr(repo, "finalize_web_language_save", None)
    if finalizer:
        finalizer(group_id, languages if replace else None)
        return
    if replace:
        repo.replace_group_languages(group_id, languages)
        return
    sealer = getattr(repo, "seal_language_prompt", None)
    if sealer:
        sealer(group_id)


def _push_update(line, group_id: str, languages: Sequence[tuple[str, str]], translate=None) -> None:
    if line is None:
        return
    notice = web_settings_notice(languages, translate=translate)
    if not notice:
        return
    try:
        line.push_text(group_id, notice[:5000])
    except Exception:
        logger.warning("Failed to push language settings update group_id=%s", group_id)


def _method(event: Dict[str, Any]) -> str:
    context = event.get("requestContext") or {}
    http = context.get("http") or {}
    method = http.get("method") or event.get("httpMethod") or "GET"
    return str(method).upper()


def _form_values(event: Dict[str, Any]) -> Dict[str, list]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded") and body:
        try:
            body = base64.b64decode(body).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return {}
    if not isinstance(body, str) or not body:
        return {}
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: list(values) for key, values in parsed.items()}


def _html_response(status: int, body: str) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
        "body": body,
    }


def _get_repo(settings):
    from .infra.neon_client import get_client
    from .infra.neon_repositories import NeonMessageRepository

    client = get_client(settings.neon_database_url)
    return NeonMessageRepository(
        client,
        max_group_languages=settings.max_group_languages,
        message_encryption_key=settings.message_encryption_key,
    )


def _get_line(settings):
    from .infra.line_api import LineApiAdapter

    return LineApiAdapter(settings.line_channel_access_token)


def _get_translator(settings):
    """案内文をグループの各言語へ訳す。作れないときは固定文に落とす。"""

    try:
        from .domain.services.interface_translation_service import InterfaceTranslationService
        from .infra.gemini_translation import GeminiTranslationAdapter

        adapter = GeminiTranslationAdapter(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.gemini_timeout_seconds,
        )
        return InterfaceTranslationService(adapter).translate
    except Exception:
        logger.warning("group language page translator unavailable", exc_info=True)
        return None
