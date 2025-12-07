import json
import logging
from typing import Any, Dict

# 月初に利用カウンタを初期化するためのプレースホルダー実装。
logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], _context: Any):
    # 実装は後続タスクで追加予定。現状は呼び出しを記録するのみ。
    logger.info("Usage counter initializer triggered: %s", event)
    return {"statusCode": 200, "body": json.dumps({"status": "pending-implementation"})}
