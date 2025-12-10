"""サブスク系テスト用の共通ヘルパー。"""

from dataclasses import dataclass
from typing import Optional, Tuple

from src.app.subscription_postback import encode_subscription_payload, decode_postback_payload


def roundtrip_subscription_payload(payload: dict) -> dict:
    encoded = encode_subscription_payload(payload)
    decoded = decode_postback_payload(encoded)
    assert decoded == payload
    return decoded


@dataclass
class DummySubscriptionRepo:
    customer_id: Optional[str] = None
    subscription_id: Optional[str] = None
    status: Optional[str] = None

    def get_subscription_detail(self, group_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        return self.customer_id, self.subscription_id, self.status

    # place-holder update for service tests
    def update_subscription_status(self, group_id: str, status: str, current_period_end):
        self.status = status

