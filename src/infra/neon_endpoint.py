from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_REGION_LABEL = re.compile(r"[a-z]{2}(?:-[a-z0-9]+)+-\d+")


@dataclass(frozen=True)
class NeonEndpointSummary:
    """接続文字列から秘密を除いて残る接続先の情報。"""

    host: str
    uses_pooler: bool
    region_hint: str


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
