from __future__ import annotations

from typing import Any, Dict, List

from app.adapters.alpaca import AlpacaAdapter
from app.logging import get_logger

logger = get_logger(__name__)

COMPOUND_ORDER_CLASSES = {"bracket", "oco", "oto"}
PRICE_FIELDS = ("stop_price", "limit_price", "trail_price", "trail_percent")


class HydratedAlpacaAdapter(AlpacaAdapter):
    """Alpaca adapter that always hydrates missing compound-order legs.

    Alpaca's open-order list can return a bracket/OCO parent with a take-profit
    price while leaving ``legs`` null or empty. The base adapter previously
    considered that row complete because one price field existed. Protection
    diagnostics then saw only the parent limit order and incorrectly classified
    the position as partially protected. This adapter refreshes compound parents
    with ``nested=true`` before diagnostics consume them.
    """

    async def get_broker_order(self, broker_order_id: str) -> Dict[str, Any]:
        order = await self._get_json(
            f"/v2/orders/{broker_order_id}?nested=true"
        )
        return self._order_snapshot(order)

    @staticmethod
    def _needs_detail(item: Dict[str, Any]) -> bool:
        if not item.get("id"):
            return False
        has_any_price = any(
            item.get(key) not in (None, "") for key in PRICE_FIELDS
        )
        order_class = str(item.get("order_class") or "").strip().lower()
        missing_compound_legs = (
            order_class in COMPOUND_ORDER_CLASSES
            and not item.get("legs")
        )
        return not has_any_price or missing_compound_legs

    @staticmethod
    def _merge_detail(
        item: Dict[str, Any], detail: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = dict(item)
        for key, value in detail.items():
            if key == "legs":
                if isinstance(value, list):
                    merged[key] = value
                continue
            if value not in (None, ""):
                merged[key] = value
        return merged

    async def get_open_orders(self) -> List[Dict[str, Any]]:
        orders = await self._get_json(
            "/v2/orders?status=open&limit=100&nested=true"
        )
        snapshots = [self._order_snapshot(item) for item in orders]
        hydrated: List[Dict[str, Any]] = []
        for item in snapshots:
            if self._needs_detail(item):
                try:
                    detail = await self.get_broker_order(str(item["id"]))
                    item = self._merge_detail(item, detail)
                except Exception as exc:  # pragma: no cover - broker fallback
                    logger.warning(
                        "Failed to hydrate nested Alpaca order details; using list snapshot.",
                        extra={
                            "broker_order_id": item.get("id"),
                            "order_class": item.get("order_class"),
                            "error": str(exc),
                        },
                    )
            hydrated.append(item)
        return hydrated
