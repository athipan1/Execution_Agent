from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.adapters.base import BrokerAdapter


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def order_age_minutes(order: Dict[str, Any], *, now: Optional[datetime] = None) -> Optional[float]:
    submitted_at = _as_datetime(order.get("submitted_at"))
    if not submitted_at:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - submitted_at).total_seconds() / 60.0)


def is_stale_order(order: Dict[str, Any], max_age_minutes: Optional[int] = None, *, now: Optional[datetime] = None) -> bool:
    max_age = int(max_age_minutes if max_age_minutes is not None else settings.MAX_STALE_OPEN_ORDER_AGE_MINUTES)
    if max_age <= 0:
        return False
    age = order_age_minutes(order, now=now)
    return age is not None and age > max_age


def classify_open_orders(open_orders: List[Dict[str, Any]], max_age_minutes: Optional[int] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    stale = []
    fresh = []
    unknown_age = []
    for order in open_orders or []:
        age = order_age_minutes(order, now=now)
        enriched = {**order, "age_minutes": round(age, 2) if age is not None else None}
        if age is None:
            unknown_age.append(enriched)
        elif is_stale_order(order, max_age_minutes, now=now):
            stale.append(enriched)
        else:
            fresh.append(enriched)
    return {
        "open_order_count": len(open_orders or []),
        "stale_order_count": len(stale),
        "fresh_order_count": len(fresh),
        "unknown_age_order_count": len(unknown_age),
        "stale_orders": stale,
        "fresh_orders": fresh,
        "unknown_age_orders": unknown_age,
        "max_stale_open_order_age_minutes": int(max_age_minutes if max_age_minutes is not None else settings.MAX_STALE_OPEN_ORDER_AGE_MINUTES),
    }


class BrokerCleanupService:
    def __init__(self, adapter: BrokerAdapter):
        self.adapter = adapter

    async def cleanup_status(self, max_age_minutes: Optional[int] = None) -> Dict[str, Any]:
        account = await self.adapter.get_account()
        positions = await self.adapter.get_positions()
        open_orders = await self.adapter.get_open_orders()
        classified = classify_open_orders(open_orders, max_age_minutes)
        return {
            "broker": account.get("broker"),
            "paper": account.get("paper"),
            "account_status": account.get("status"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "equity": account.get("equity") or account.get("portfolio_value"),
            "position_count": len(positions or []),
            "positions": positions,
            **classified,
            "cleanup_required": classified["stale_order_count"] > 0 or str(account.get("buying_power", "0")) in {"0", "0.0", "0.00"},
        }

    async def cancel_stale_open_orders(self, max_age_minutes: Optional[int] = None, *, dry_run: bool = True) -> Dict[str, Any]:
        open_orders = await self.adapter.get_open_orders()
        classified = classify_open_orders(open_orders, max_age_minutes)
        cancelled = []
        failed = []
        for order in classified["stale_orders"]:
            if dry_run:
                cancelled.append({"dry_run": True, "broker_order_id": order.get("id"), "symbol": order.get("symbol"), "side": order.get("side"), "qty": order.get("qty"), "age_minutes": order.get("age_minutes")})
                continue
            result = await self.adapter.cancel_open_order(order)
            result["age_minutes"] = order.get("age_minutes")
            if str(result.get("status")).lower() in {"cancelled", "canceled", "orderstatus.cancelled"}:
                cancelled.append(result)
            else:
                failed.append(result)
        return {
            "dry_run": dry_run,
            "target": "stale_open_orders",
            "max_stale_open_order_age_minutes": classified["max_stale_open_order_age_minutes"],
            "matched_count": classified["stale_order_count"],
            "cancelled_count": len(cancelled) if not dry_run else 0,
            "would_cancel_count": len(cancelled) if dry_run else 0,
            "failed_count": len(failed),
            "cancelled": cancelled,
            "failed": failed,
            "remaining_fresh_count": classified["fresh_order_count"],
            "unknown_age_order_count": classified["unknown_age_order_count"],
        }

    async def cancel_all_open_orders(self, *, dry_run: bool = True) -> Dict[str, Any]:
        open_orders = await self.adapter.get_open_orders()
        cancelled = []
        failed = []
        for order in open_orders or []:
            if dry_run:
                cancelled.append({"dry_run": True, "broker_order_id": order.get("id"), "symbol": order.get("symbol"), "side": order.get("side"), "qty": order.get("qty")})
                continue
            result = await self.adapter.cancel_open_order(order)
            if str(result.get("status")).lower() in {"cancelled", "canceled", "orderstatus.cancelled"}:
                cancelled.append(result)
            else:
                failed.append(result)
        return {
            "dry_run": dry_run,
            "target": "all_open_orders",
            "matched_count": len(open_orders or []),
            "cancelled_count": len(cancelled) if not dry_run else 0,
            "would_cancel_count": len(cancelled) if dry_run else 0,
            "failed_count": len(failed),
            "cancelled": cancelled,
            "failed": failed,
        }
