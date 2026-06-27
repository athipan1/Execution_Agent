from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.adapters.base import BrokerAdapter


PROTECTIVE_ORDER_TYPES = {"stop", "stop_loss", "stop_limit", "trailing_stop"}
ENTRY_ORDER_TYPES = {"market", "limit", "stop_limit"}
BUY_SIDES = {"buy"}
SELL_SIDES = {"sell"}


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


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def order_age_minutes(order: Dict[str, Any], *, now: Optional[datetime] = None) -> Optional[float]:
    submitted_at = _as_datetime(order.get("submitted_at"))
    if not submitted_at:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - submitted_at).total_seconds() / 60.0)


def is_protective_exit_order(order: Dict[str, Any], position_symbols: Optional[set[str]] = None) -> bool:
    """Return true for open exit orders that appear to protect an existing position.

    Alpaca GTC stop orders can intentionally stay open for days as protective
    exits. These should be reported separately and must not be auto-cancelled by
    generic stale-entry cleanup.
    """
    side = _normalized(order.get("side"))
    order_type = _normalized(order.get("type") or order.get("order_type"))
    symbol = _symbol(order.get("symbol"))
    if side not in SELL_SIDES or order_type not in PROTECTIVE_ORDER_TYPES:
        return False
    if position_symbols is None:
        return True
    return symbol in position_symbols


def is_entry_order(order: Dict[str, Any]) -> bool:
    side = _normalized(order.get("side"))
    order_type = _normalized(order.get("type") or order.get("order_type"))
    return side in BUY_SIDES and order_type in ENTRY_ORDER_TYPES


def is_stale_order(
    order: Dict[str, Any],
    max_age_minutes: Optional[int] = None,
    *,
    now: Optional[datetime] = None,
    position_symbols: Optional[set[str]] = None,
) -> bool:
    max_age = int(max_age_minutes if max_age_minutes is not None else settings.MAX_STALE_OPEN_ORDER_AGE_MINUTES)
    if max_age <= 0:
        return False
    if is_protective_exit_order(order, position_symbols):
        return False
    age = order_age_minutes(order, now=now)
    return age is not None and age > max_age


def classify_open_orders(
    open_orders: List[Dict[str, Any]],
    max_age_minutes: Optional[int] = None,
    *,
    positions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    position_symbols = {_symbol(position.get("symbol")) for position in positions or [] if position.get("symbol")}
    stale = []
    fresh = []
    unknown_age = []
    protective = []
    stale_entry = []
    stale_non_entry = []

    for order in open_orders or []:
        age = order_age_minutes(order, now=now)
        enriched = {
            **order,
            "age_minutes": round(age, 2) if age is not None else None,
            "protective_exit_order": is_protective_exit_order(order, position_symbols),
            "entry_order": is_entry_order(order),
        }
        if enriched["protective_exit_order"]:
            protective.append(enriched)
        elif age is None:
            unknown_age.append(enriched)
        elif is_stale_order(order, max_age_minutes, now=now, position_symbols=position_symbols):
            stale.append(enriched)
            if enriched["entry_order"]:
                stale_entry.append(enriched)
            else:
                stale_non_entry.append(enriched)
        else:
            fresh.append(enriched)

    return {
        "open_order_count": len(open_orders or []),
        "stale_order_count": len(stale),
        "fresh_order_count": len(fresh),
        "unknown_age_order_count": len(unknown_age),
        "protective_order_count": len(protective),
        "stale_entry_order_count": len(stale_entry),
        "stale_non_entry_order_count": len(stale_non_entry),
        "stale_orders": stale,
        "stale_entry_orders": stale_entry,
        "stale_non_entry_orders": stale_non_entry,
        "fresh_orders": fresh,
        "protective_orders": protective,
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
        classified = classify_open_orders(open_orders, max_age_minutes, positions=positions)
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

    async def cancel_stale_open_orders(
        self,
        max_age_minutes: Optional[int] = None,
        *,
        dry_run: bool = True,
        include_protective: bool = False,
    ) -> Dict[str, Any]:
        positions = await self.adapter.get_positions()
        open_orders = await self.adapter.get_open_orders()
        classified = classify_open_orders(open_orders, max_age_minutes, positions=positions)
        targets = list(classified["stale_orders"])
        if include_protective:
            targets.extend(classified["protective_orders"])
        cancelled = []
        skipped = []
        failed = []
        for order in targets:
            if order.get("protective_exit_order") and not include_protective:
                skipped.append({
                    "reason": "protective_exit_order_not_cancelled",
                    "broker_order_id": order.get("id"),
                    "symbol": order.get("symbol"),
                    "side": order.get("side"),
                    "type": order.get("type") or order.get("order_type"),
                    "qty": order.get("qty"),
                    "age_minutes": order.get("age_minutes"),
                })
                continue
            if dry_run:
                cancelled.append({
                    "dry_run": True,
                    "broker_order_id": order.get("id"),
                    "symbol": order.get("symbol"),
                    "side": order.get("side"),
                    "type": order.get("type") or order.get("order_type"),
                    "qty": order.get("qty"),
                    "age_minutes": order.get("age_minutes"),
                    "protective_exit_order": order.get("protective_exit_order"),
                })
                continue
            result = await self.adapter.cancel_open_order(order)
            result["age_minutes"] = order.get("age_minutes")
            result["protective_exit_order"] = order.get("protective_exit_order")
            if str(result.get("status")).lower() in {"cancelled", "canceled", "orderstatus.cancelled"}:
                cancelled.append(result)
            else:
                failed.append(result)
        return {
            "dry_run": dry_run,
            "target": "stale_open_orders",
            "include_protective": include_protective,
            "max_stale_open_order_age_minutes": classified["max_stale_open_order_age_minutes"],
            "matched_count": len(targets),
            "cancelled_count": len(cancelled) if not dry_run else 0,
            "would_cancel_count": len(cancelled) if dry_run else 0,
            "failed_count": len(failed),
            "skipped_count": len(skipped),
            "cancelled": cancelled,
            "skipped": skipped,
            "failed": failed,
            "remaining_fresh_count": classified["fresh_order_count"],
            "protective_order_count": classified["protective_order_count"],
            "unknown_age_order_count": classified["unknown_age_order_count"],
            "classification": classified,
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
