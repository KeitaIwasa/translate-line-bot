from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

_REGION_LABEL = re.compile(r"[a-z]{2}(?:-[a-z0-9]+)+-\d+")


@dataclass(frozen=True)
class NeonEndpointSummary:
    """接続文字列から秘密を除いて残る接続先の情報。"""

    host: str
    uses_pooler: bool
    region_hint: str


_POOLER_OFF = frozenset({"0", "false", "no", "off"})


def neon_pooler_enabled() -> bool:
    """NEON_USE_POOLER が明示的に偽のときだけ直接接続にする。未設定は pooler を使う。"""

    raw = os.environ.get("NEON_USE_POOLER", "")
    if not isinstance(raw, str):
        return True
    return raw.strip().lower() not in _POOLER_OFF


def pooler_host(host: str) -> str:
    """Neon の直接接続ホストを pooled 接続のホストにする。対象外はそのまま返す。"""

    if not host or not host.lower().endswith(".neon.tech"):
        return host
    labels = host.split(".")
    if labels[0].lower().endswith("-pooler"):
        return host
    labels[0] = f"{labels[0]}-pooler"
    return ".".join(labels)


def resolve_neon_dsn(dsn: str, *, use_pooler: bool | None = None) -> str:
    """pooler を使う設定なら、接続文字列のホストだけを pooled 接続に変える。

    ユーザー名、パスワード、データベース名は変えない。localhost など Neon 以外も変えない。
    """

    if use_pooler is None:
        use_pooler = neon_pooler_enabled()
    if not use_pooler:
        return dsn
    return _rewrite_dsn_host(dsn, pooler_host)


def summarize_neon_dsn(dsn: str) -> NeonEndpointSummary:
    """ホスト名だけを見る。パスワードやユーザー名は返さない。"""

    host = _host_from_dsn(dsn or "")
    region = ""
    for label in host.split("."):
        if _REGION_LABEL.fullmatch(label):
            region = label
            break
    return NeonEndpointSummary(
        host=host,
        uses_pooler="-pooler" in host,
        region_hint=region,
    )


def connection_kwargs() -> dict:
    """読み取りのたびに COMMIT しない。プール経由では prepared statement を使わない。"""

    return {
        "autocommit": True,
        "prepare_threshold": None,
        "connect_timeout": 5,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }


def log_neon_endpoint(summary: NeonEndpointSummary) -> None:
    lambda_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    logger.info(
        "Neon endpoint | host=%s pooler=%s region_hint=%s lambda_region=%s",
        summary.host or "unknown",
        summary.uses_pooler,
        summary.region_hint or "unknown",
        lambda_region or "unknown",
    )
    if summary.host and not summary.uses_pooler:
        logger.warning(
            "Neon DSN host has no -pooler suffix | host=%s region_hint=%s",
            summary.host,
            summary.region_hint or "unknown",
        )
    if summary.region_hint and lambda_region and summary.region_hint != lambda_region:
        logger.warning(
            "Neon region differs from Lambda region | neon_region=%s lambda_region=%s host=%s",
            summary.region_hint,
            lambda_region,
            summary.host,
        )


def _rewrite_dsn_host(dsn: str, rewrite) -> str:
    text = dsn.strip()
    if "://" in text:
        parsed = urlparse(text)
        host = parsed.hostname or ""
        new_host = rewrite(host)
        if not host or new_host == host:
            return dsn
        userinfo, sep, _location = parsed.netloc.rpartition("@")
        location = f"{new_host}:{parsed.port}" if parsed.port else new_host
        netloc = f"{userinfo}{sep}{location}" if sep else location
        return urlunparse(parsed._replace(netloc=netloc))

    match = re.search(r"((?:^|\s)host=)(['\"]?)([^\s'\"]+)\2", text)
    if not match:
        return dsn
    new_host = rewrite(match.group(3))
    if new_host == match.group(3):
        return dsn
    quote = match.group(2)
    replaced = f"{match.group(1)}{quote}{new_host}{quote}"
    return f"{text[:match.start()]}{replaced}{text[match.end():]}"


def _host_from_dsn(dsn: str) -> str:
    text = dsn.strip()
    if "://" in text:
        host = urlparse(text).hostname or ""
        if host:
            return host
    match = re.search(r"(?:^|\s)host=([^\s]+)", text)
    if not match:
        return ""
    return match.group(1).strip("'\"")
