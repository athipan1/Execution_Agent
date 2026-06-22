from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models import Order, OrderSide


class BrokerPreflightError(RuntimeError):
    pass


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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


def _order_notional(order: Order) -> float:
    price = _as_float(order.price, 0.0)
    return max(0.0, price * float(order.quantity or 0))


def _stale_open_orders(open_orders: List[Dict[str, Any]], max_age_minutes: int) -> List[Dict[str, Any]]:
    if max_age_minutes <= 0:
        return []
    now = datetime.now(timezone.utc)
    stale = []
    for item in open_orders or []:
        submitted_at = _as_datetime(item.get("submitted_at"))
        if not submitted_at:
            continue
        age_minutes = (now - submitted_at).total_seconds() / 60.0
        if age_minutes > max_age_minutes:
            stale.append({**item, "age_minutes": round(age_minutes, 2)})
    return stale


def build_broker_preflight_snapshot(account: Dict[str, Any], positions: List[Dict[str, Any]], open_orders: List[Dict[str, Any]], order: Optional[Order] = None) -> Dict[str, Any]:
    buying_power = _as_float(account.get("buying_power"), 0.0)
    cash = _as_float(account.get("cash"), 0.0)
    equity = _as_float(account.get("equity") or account.get("portfolio_value"), 0.0)
    order_notional = _order_notional(order) if order else 0.0
    stale_orders = _stale_open_orders(open_orders, int(settings.MAX_STALE_OPEN_ORDER_AGE_MINUTES))
    return {
        "broker": account.get("broker"),
        "paper": account.get("paper"),
        "account_status": account.get("status"),
        "buying_power": buying_power,
        "cash": cash,
        "equity": equity,
        "trading_blocked": bool(account.get("trading_blocked")),
        "transfers_blocked": bool(account.get("transfers_blocked")),
        "account_blocked": bool(account.get("account_blocked")),
        "order_symbol": order.symbol if order else None,
        "order_side": str(order.side.value if hasattr(order.side, "value") else order.side) if order else None,
        "order_quantity": int(order.quantity or 0) if order else 0,
        "order_notional": round(order_notional, 2),
        "buying_power_after_order": round(buying_power - order_notional, 2),
        "position_count": len(positions or []),
        "open_order_count": len(open_orders or []),
        "stale_open_order_count": len(stale_orders),
        "stale_open_orders": stale_orders,
    }


def validate_broker_preflight(account: Dict[str, Any], positions: List[Dict[str, Any]], open_orders: List[Dict[str, Any]], order: Order) -> Dict[str, Any]:
    snapshot = build_broker_preflight_snapshot(account, positions, open_orders, order)
    violations: list[str] = []
    warnings: list[str] = []

    if settings.FAIL_ON_ACCOUNT_RESTRICTED and (
        snapshot["trading_blocked"] or snapshot["account_blocked"] or str(snapshot["account_status"] or "").upper() not in {"ACTIVE", ""}
    ):
        violations.append("broker_account_restricted")

    if settings.BLOCK_BUY_WHEN_NO_BUYING_POWER and order.side == OrderSide.BUY:
        if snapshot["buying_power"] <= 0:
            violations.append("buying_power_unavailable")
        if snapshot["buying_power_after_order"] < float(settings.MIN_BUYING_POWER_AFTER_ORDER):
            violations.append("insufficient_buying_power_after_order")

    if settings.FAIL_ON_STALE_OPEN_ORDERS and snapshot["stale_open_order_count"] > 0:
        violations.append("stale_open_orders_present")
    elif snapshot["stale_open_order_count"] > 0:
        warnings.append("stale_open_orders_present")

    snapshot["approved"] = len(violations) == 0
    snapshot["violations"] = violations
    snapshot["warnings"] = warnings
    if violations:
        raise BrokerPreflightError(f"Broker preflight rejected order {order.order_id}: {violations}")
    return snapshot
