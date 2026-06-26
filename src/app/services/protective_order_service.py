from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

from app.models import Order, OrderSide, OrderType


class ProtectiveOrderError(ValueError):
    """Raised when an order cannot be protected by a broker-side exit order."""


def _as_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProtectiveOrderError(f"{field_name} must be a number") from exc
    if result <= 0:
        raise ProtectiveOrderError(f"{field_name} must be greater than zero")
    return result


def alpaca_price(value: Any, field_name: str = "price") -> str:
    """Return Alpaca-compatible price text.

    Alpaca rejects sub-penny increments for most US equity orders. Prices at or
    above $1 must not exceed two decimal places, while prices below $1 can use
    up to four decimal places. Use half-up rounding so a calculated protective
    stop like 92.984 becomes 92.98 instead of being rejected by the broker.
    """
    amount = Decimal(str(_as_float(value, field_name)))
    quantum = Decimal("0.01") if amount >= Decimal("1") else Decimal("0.0001")
    rounded = amount.quantize(quantum, rounding=ROUND_HALF_UP)
    return format(rounded.normalize(), "f")


def _normalize_side(value: Any) -> str:
    return str(value or "").strip().lower()


def expected_exit_side(order: Order) -> str:
    return OrderSide.SELL.value if order.side == OrderSide.BUY else OrderSide.BUY.value


def protection_plan(order: Order) -> Optional[Dict[str, Any]]:
    plan = order.protective_exit or order.guard_plan
    return dict(plan) if isinstance(plan, dict) else None


def validate_protection_plan(order: Order, *, required: bool = False) -> Optional[Dict[str, Any]]:
    """Return a normalized protective plan or raise when required/invalid.

    The plan comes from Risk_Agent/Manager and must describe the protective exit,
    not the entry. For a buy entry, the protective side must be sell; for a sell
    entry, the protective side must be buy. Quantity must match the approved
    final order quantity so the broker never receives a naked entry.
    """
    plan = protection_plan(order)
    if not plan:
        if required:
            raise ProtectiveOrderError("guard_plan or protective_exit is required")
        return None

    symbol = str(plan.get("symbol") or order.symbol).upper()
    if symbol != order.symbol.upper():
        raise ProtectiveOrderError("protective symbol must match entry order symbol")

    side = _normalize_side(plan.get("side"))
    if side != expected_exit_side(order):
        raise ProtectiveOrderError(
            f"protective side must be {expected_exit_side(order)} for {order.side.value} entries"
        )

    quantity = int(_as_float(plan.get("quantity", order.quantity), "protective quantity"))
    if quantity != int(order.quantity):
        raise ProtectiveOrderError("protective quantity must match entry order quantity")

    trigger_price = _as_float(
        plan.get("trigger_price") or plan.get("stop_price") or plan.get("protection_price"),
        "protective trigger_price",
    )

    if order.price:
        if order.side == OrderSide.BUY and trigger_price >= float(order.price):
            raise ProtectiveOrderError("buy entry stop price must be below the entry price")
        if order.side == OrderSide.SELL and trigger_price <= float(order.price):
            raise ProtectiveOrderError("sell entry stop price must be above the entry price")

    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "trigger_price": trigger_price,
        "time_in_force": str(plan.get("time_in_force") or order.time_in_force.value).lower(),
        "source": plan.get("source") or "risk_guard_plan",
    }


def build_alpaca_entry_payload(order: Order, *, require_protection: bool = False) -> Dict[str, Any]:
    """Build an Alpaca entry order payload with broker-side stop protection.

    Stop-only protection uses Alpaca's OTO order class. If a take-profit price is
    provided in the plan, the payload is upgraded to a bracket order.
    """
    payload: Dict[str, Any] = {
        "side": order.side.value,
        "symbol": order.symbol,
        "qty": str(order.quantity),
        "type": order.order_type.value,
        "time_in_force": order.time_in_force.value.lower(),
    }

    if order.order_type == OrderType.LIMIT and order.price:
        payload["limit_price"] = alpaca_price(order.price, "limit_price")

    plan = validate_protection_plan(order, required=require_protection)
    if not plan:
        return payload

    raw_plan = protection_plan(order) or {}
    take_profit_price = raw_plan.get("take_profit_price") or raw_plan.get("take_profit")
    payload["order_class"] = "bracket" if take_profit_price else "oto"
    payload["stop_loss"] = {"stop_price": alpaca_price(plan["trigger_price"], "stop_loss.stop_price")}

    if take_profit_price:
        payload["take_profit"] = {"limit_price": alpaca_price(take_profit_price, "take_profit.limit_price")}

    return payload
