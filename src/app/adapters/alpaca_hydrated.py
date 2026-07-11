from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from app.adapters.alpaca import AlpacaAdapter
from app.logging import get_logger

logger = get_logger(__name__)

COMPOUND_ORDER_CLASSES = {"bracket", "oco", "oto"}
PRICE_FIELDS = ("stop_price", "limit_price", "trail_price", "trail_percent")
CANCEL_CONFIRMED_STATUSES = {"canceled", "cancelled"}
CANCEL_CONFIRMATION_ATTEMPTS = 30
CANCEL_CONFIRMATION_INTERVAL_SECONDS = 0.5


def _status_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


class HydratedAlpacaAdapter(AlpacaAdapter):
    """Alpaca adapter with reliable compound-order and cancellation hydration.

    Alpaca's open-order list can return a bracket/OCO parent with a take-profit
    price while leaving ``legs`` null or empty. The base adapter previously
    considered that row complete because one price field existed. Protection
    diagnostics then saw only the parent limit order and incorrectly classified
    the position as partially protected. This adapter refreshes compound parents
    with ``nested=true`` before diagnostics consume them.

    Alpaca can also acknowledge DELETE with HTTP 204 while the order remains in
    ``pending_cancel`` and continues reserving position quantity. Protection
    reconciliation must not submit a full-position replacement until the broker
    confirms the old order is actually canceled.
    """

    async def get_broker_order(self, broker_order_id: str) -> Dict[str, Any]:
        order = await self._get_json(
            f"/v2/orders/{broker_order_id}?nested=true"
        )
        return self._order_snapshot(order)

    async def cancel_order(self, broker_order_id: str) -> dict:
        result = await super().cancel_order(broker_order_id)
        if _status_text(result.get("status")) not in CANCEL_CONFIRMED_STATUSES:
            return result

        observations: List[Dict[str, Any]] = []
        last_broker_status = ""
        for attempt in range(1, CANCEL_CONFIRMATION_ATTEMPTS + 1):
            try:
                detail = await self.get_broker_order(broker_order_id)
                last_broker_status = _status_text(detail.get("status"))
                observations.append(
                    {
                        "attempt": attempt,
                        "broker_status": last_broker_status or "unknown",
                    }
                )
                if last_broker_status in CANCEL_CONFIRMED_STATUSES:
                    return {
                        **result,
                        "cancel_requested": True,
                        "cancel_confirmed": True,
                        "broker_status": last_broker_status,
                        "confirmation_attempts": attempt,
                    }
            except Exception as exc:  # pragma: no cover - defensive broker polling
                observations.append(
                    {
                        "attempt": attempt,
                        "error": str(exc),
                    }
                )

            if attempt < CANCEL_CONFIRMATION_ATTEMPTS:
                await asyncio.sleep(CANCEL_CONFIRMATION_INTERVAL_SECONDS)

        logger.error(
            "Alpaca accepted cancellation but did not confirm terminal status before timeout.",
            extra={
                "broker_order_id": broker_order_id,
                "last_broker_status": last_broker_status,
                "confirmation_attempts": CANCEL_CONFIRMIRMATION_ATTEMPTS if False else CANCEL_CONFIRMATION_ATTEMPTS,
            },
        )
        return {
            "status": "error",
            "message": (
                "Alpaca accepted the cancellation request, but the order remained "
                "active or pending_cancel until the confirmation timeout. Replacement "
                "submission was blocked to avoid an insufficient-quantity race."
            ),
            "broker_order_id": broker_order_id,
            "cancel_requested": True,
            "cancel_confirmed": False,
            "last_broker_status": last_broker_status or None,
            "confirmation_attempts": CANCEL_CONFIRMATION_ATTEMPTS,
            "recent_observations": observations[-5:],
        }

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
