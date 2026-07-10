from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List

PROTECTIVE_ORDER_TYPES = {"stop", "stop_limit", "trailing_stop"}
TAKE_PROFIT_TYPES = {"limit"}
ACTIVE_ORDER_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "calculated",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "stopped",
}


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _quantity(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _has_value(value: Any) -> bool:
    return value not in (None, "", 0, "0", "0.0", "0.00")


def _is_active(order: Dict[str, Any]) -> bool:
    status = _lower(order.get("status"))
    return not status or status in ACTIVE_ORDER_STATUSES


def _is_protective_stop(order: Dict[str, Any]) -> bool:
    side = _lower(order.get("side"))
    order_type = _lower(order.get("type") or order.get("order_type"))
    if side != "sell" or order_type not in PROTECTIVE_ORDER_TYPES or not _is_active(order):
        return False
    if order_type == "trailing_stop":
        return _has_value(order.get("trail_price")) or _has_value(order.get("trail_percent"))
    return _has_value(order.get("stop_price") or order.get("trigger_price"))


def _is_take_profit(order: Dict[str, Any]) -> bool:
    side = _lower(order.get("side"))
    order_type = _lower(order.get("type") or order.get("order_type"))
    return (
        side == "sell"
        and order_type in TAKE_PROFIT_TYPES
        and _has_value(order.get("limit_price"))
        and _is_active(order)
    )


def _flatten_orders(orders: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened: List[Dict[str, Any]] = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        flattened.append(order)
        legs = order.get("legs") or []
        if not isinstance(legs, list):
            continue
        for leg in _flatten_orders(legs):
            next_leg = dict(leg)
            next_leg.setdefault("parent_order_id", order.get("id"))
            next_leg.setdefault("parent_order_class", order.get("order_class"))
            next_leg.setdefault("symbol", order.get("symbol"))
            flattened.append(next_leg)
    return flattened


def _covered_quantity(orders: Iterable[Dict[str, Any]]) -> float:
    return sum(_quantity(order.get("qty") or order.get("quantity")) for order in orders)


def _order_summary(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": order.get("id") or order.get("broker_order_id") or order.get("order_id"),
        "parent_order_id": order.get("parent_order_id"),
        "symbol": _upper(order.get("symbol")),
        "side": order.get("side"),
        "qty": order.get("qty") or order.get("quantity"),
        "type": order.get("type") or order.get("order_type"),
        "order_class": order.get("order_class"),
        "status": order.get("status"),
        "stop_price": order.get("stop_price") or order.get("trigger_price"),
        "limit_price": order.get("limit_price"),
        "trail_price": order.get("trail_price"),
        "trail_percent": order.get("trail_percent"),
        "submitted_at": order.get("submitted_at"),
        "created_at": order.get("created_at"),
    }


def build_protection_diagnostics(
    positions: List[Dict[str, Any]],
    open_orders: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Inspect broker positions and concrete active order legs without mutations."""
    flattened_orders = _flatten_orders(open_orders)
    orders_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for order in flattened_orders:
        symbol = _upper(order.get("symbol"))
        if symbol:
            orders_by_symbol[symbol].append(order)

    rows: List[Dict[str, Any]] = []
    summary = {
        "position_count": len(positions),
        "open_order_count": len(open_orders),
        "flattened_order_count": len(flattened_orders),
        "protected_position_count": 0,
        "unprotected_position_count": 0,
        "partially_protected_position_count": 0,
        "stop_only_count": 0,
        "bracket_protected_count": 0,
        "needs_bracket_upgrade_count": 0,
        "diagnostic_only": True,
        "orders_submitted": False,
    }

    for position in positions:
        symbol = _upper(position.get("symbol"))
        position_qty = _quantity(position.get("qty") or position.get("quantity"))
        symbol_orders = orders_by_symbol.get(symbol, [])
        stop_orders = [order for order in symbol_orders if _is_protective_stop(order)]
        take_profit_orders = [order for order in symbol_orders if _is_take_profit(order)]
        stop_covered_qty = _covered_quantity(stop_orders)
        take_profit_covered_qty = _covered_quantity(take_profit_orders)
        fully_stop_protected = position_qty > 0 and stop_covered_qty >= position_qty
        fully_take_profit_protected = position_qty > 0 and take_profit_covered_qty >= position_qty
        has_protective_stop = bool(stop_orders)
        has_take_profit = bool(take_profit_orders)
        has_position = bool(symbol) and position_qty > 0

        if fully_stop_protected and fully_take_profit_protected:
            protection_status = "bracket_protected"
            recommended_action = "none"
            summary["protected_position_count"] += 1
            summary["bracket_protected_count"] += 1
        elif fully_stop_protected and not has_take_profit:
            protection_status = "stop_only"
            recommended_action = "needs_bracket_upgrade"
            summary["protected_position_count"] += 1
            summary["stop_only_count"] += 1
            summary["needs_bracket_upgrade_count"] += 1
        elif has_protective_stop or has_take_profit:
            protection_status = "partially_protected"
            recommended_action = "reconcile_protective_order_quantities"
            summary["partially_protected_position_count"] += 1
        else:
            protection_status = "unprotected"
            recommended_action = "needs_protective_order"
            summary["unprotected_position_count"] += 1

        rows.append(
            {
                "symbol": symbol,
                "position_qty": position.get("qty") or position.get("quantity"),
                "current_price": position.get("current_price"),
                "avg_entry_price": position.get("avg_entry_price"),
                "has_position": has_position,
                "has_protective_stop": has_protective_stop,
                "has_take_profit": has_take_profit,
                "has_bracket": fully_stop_protected and fully_take_profit_protected,
                "stop_covered_qty": stop_covered_qty,
                "take_profit_covered_qty": take_profit_covered_qty,
                "unprotected_stop_qty": max(0.0, position_qty - stop_covered_qty),
                "unprotected_take_profit_qty": max(0.0, position_qty - take_profit_covered_qty),
                "protection_status": protection_status,
                "recommended_action": recommended_action,
                "orders_submitted": False,
                "diagnostic_only": True,
                "open_order_count": len(symbol_orders),
                "protective_orders": [_order_summary(order) for order in stop_orders],
                "take_profit_orders": [_order_summary(order) for order in take_profit_orders],
                "open_orders": [_order_summary(order) for order in symbol_orders],
            }
        )

    return {
        "status": "success",
        "mode": "diagnostic_only",
        "safety": "read_only_no_orders_submitted",
        "summary": summary,
        "positions": rows,
    }
