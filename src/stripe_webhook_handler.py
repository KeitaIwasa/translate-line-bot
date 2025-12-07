import json
import logging
from typing import Any, Dict

# Stripe Webhook 用のプレースホルダー実装。実処理は後続タスクで追加する。
logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], _context: Any):
    # 受信イベントを記録するだけのスタブ実装
    logger.info("Stripe webhook event received: %s", event)
    return {"statusCode": 200, "body": json.dumps({"status": "pending-implementation"})}
