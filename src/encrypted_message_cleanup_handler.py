from __future__ import annotations

import json
import logging
from typing import Any, Dict

from .config import get_settings
from .infra.neon_client import get_client
from .infra.neon_repositories import NeonMessageRepository

logger = logging.getLogger(__name__)


def lambda_handler(_event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    settings = get_settings()
    client = get_client(settings.neon_database_url)
    repo = NeonMessageRepository(
        client,
        max_group_languages=settings.max_group_languages,
        message_encryption_key=settings.message_encryption_key,
    )
    deleted_count = repo.delete_expired_encrypted_messages(retention_days=7)
    logger.info("Expired encrypted messages deleted", extra={"deleted_count": deleted_count})
    return {
        "statusCode": 200,
        "body": json.dumps({"deletedCount": deleted_count}),
    }
