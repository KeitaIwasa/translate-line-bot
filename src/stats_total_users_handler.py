from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .config import get_settings
from .infra.neon_client import get_client
from .infra.neon_repositories import NeonMessageRepository

logger = logging.getLogger(__name__)
settings = get_settings()
_repo: Optional[NeonMessageRepository] = None


def lambda_handler(_event: Dict[str, Any], _context) -> Dict[str, Any]:
    try:
        total_users = _get_repo().get_total_distinct_users()
        body = {
            "totalUsers": total_users,
            "metric": "distinct_user_ids",
            "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        return _json_response(200, body)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to fetch total users")
        return _json_response(500, {"message": "Internal Server Error"})


def _get_repo() -> NeonMessageRepository:
    global _repo
    if _repo is None:
        client = get_client(settings.neon_database_url)
        _repo = NeonMessageRepository(client, max_group_languages=settings.max_group_languages)
    return _repo


def _json_response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
