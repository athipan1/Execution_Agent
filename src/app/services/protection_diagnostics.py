from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

PROTECTIVE_ORDER_TYPES = {"stop", "stop_limit", "trailing_stop"}
PROTECTIVE_ORDER_CLASSES = {"oto", "bracket"}
TAKE_PROFIT_TYPES = {"limit"}


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _quantity(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_protective_stop(order: Dict[str, Any]) -> bool:
    side = _lower(order.get("side"))
    order_type = _lower(order.get("type") or order.get("order_type"))
    order_class = _lower(order.get("order_class"))
    return side == "sell" and (
        order_type in PROTECTIVE_ORDER_TYPES or order_class in PROTECTIVE_ORDER_CLASSES
    )


def _is_take_profit(order: Dict[str, Any]) -> bool:
    side = _lower(order.get("side"))
    order_type = _lower(order.get("type") or order.get("order_type"))
    order_class = _lower(order.get("order_class"))
    return side == "sell" and (
        order_type in TAKE_PROFIT_TYPES or order_class == "bracket"
    )


def _order_summary(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": order.get("id") or order.get("broker_order_id") or order.get("order_id"),
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
    """Inspect broker positions/open orders without mutating broker state.

    This is report-only by design. It detects legacy stop-only OTO protection so
    Manager/Database can flag positions that should be reviewed before any
    cancel/replace workflow is implemented.
    """
    orders_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for order in open_orders:
        symbol = _upper(order.get("symbol"))
        if symbol:
            orders_by_symbol[symbol].append(order)

    rows: List[Dict[str, Any]] = []
    summary = {
        "position_count": len(positions),
        "open_order_count": len(open_orders),
        "protected_position_count": 0,
        "unprotected_position_count": 0,
        "stop_only_count": 0,
        "bracket_protected_count": 0,
        "needs_bracket_upgrade_count": 0,
        "diagnostic_only": True,
        "orders_submitted": False,
    }

    for position in positions:
        symbol = _upper(position.get("symbol"))
        symbol_orders = orders_by_symbol.get(symbol, [])
        stop_orders = [order for order in symbol_orders if _is_protective_stop(order)]
        take_profit_orders = [order for order in symbol_orders if _is_take_profit(order)]
        bracket_orders = [order for order in symbol_orders if _lower(order.get("order_class")) == "bracket"]

        has_position = bool(symbol) and _quantity(position.get("qty") or position.get("quantity")) > 0
        has_protective_stop = bool(stop_orders)
        has_take_profit = bool(take_profit_orders)
        has_bracket = bool(bracket_orders)

        if has_bracket or (has_protective_stop and has_take_profit):
            protection_status = "bracket_protected" if has_bracket else "tp_sl_protected"
            recommended_action = "none"
            summary["protected_position_count"] += 1
            if has_bracket:
                summary["bracket_protected_count"] += 1
        elif has_protective_stop:
            protection_status = "stop_only"
            recommended_action = "needs_bracket_upgrade"
            summary["protected_position_count"] += 1
            summary["stop_only_count"] += 1
            summary["needs_bracket_upgrade_count"] += 1
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
                "has_bracket": has_bracket,
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
