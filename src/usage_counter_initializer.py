from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import psycopg

logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], _context: Any):
    logger.info("Usage counter initializer triggered", extra={"event": event})
    dsn = os.getenv("NEON_DATABASE_URL")
    if not dsn:
        logger.error("NEON_DATABASE_URL missing")
        return {"statusCode": 500, "body": json.dumps({"message": "missing database url"})}

    month_key = _current_month_key()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO group_usage_counters (group_id, month_key, translation_count, created_at, updated_at)
                SELECT DISTINCT group_id, %s, 0, NOW(), NOW()
                FROM group_members
                ON CONFLICT (group_id, month_key) DO NOTHING
                """,
                (month_key,),
            )

    return {"statusCode": 200, "body": json.dumps({"status": "ok", "month_key": month_key})}


def _current_month_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"
