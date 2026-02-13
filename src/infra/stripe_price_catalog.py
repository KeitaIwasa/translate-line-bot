from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from ..domain.services.plan_policy import PRICE_DEFINITIONS, PriceDefinition


@dataclass(frozen=True)
class StripePriceCatalog:
    by_target: Dict[str, str]
    by_price_id: Dict[str, PriceDefinition]

    def resolve_target(self, target_key: str) -> Optional[str]:
        return self.by_target.get(target_key)

    def resolve_price(self, price_id: Optional[str]) -> Optional[PriceDefinition]:
        if not price_id:
            return None
        return self.by_price_id.get(price_id)


def build_price_catalog(settings) -> StripePriceCatalog:
    by_target = {
        "standard_monthly": (settings.stripe_price_standard_monthly_id or "").strip(),
        "standard_yearly": (settings.stripe_price_standard_yearly_id or "").strip(),
        "pro_monthly": (settings.stripe_price_pro_monthly_id or "").strip(),
        "pro_yearly": (settings.stripe_price_pro_yearly_id or "").strip(),
        "pro_legacy_monthly": (settings.stripe_price_pro_legacy_monthly_id or "").strip(),
    }
    by_target = {key: value for key, value in by_target.items() if value}
    by_price_id = {price_id: PRICE_DEFINITIONS[target] for target, price_id in by_target.items()}
    return StripePriceCatalog(by_target=by_target, by_price_id=by_price_id)
