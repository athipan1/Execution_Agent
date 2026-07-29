from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from typing import Any, Dict, Optional

from app.models import Order, OrderSide, OrderType

class ProtectiveOrderError(ValueError):

    """Raised when an order cannot be protected by a broker-side exit order."""

# บังคับให้ entry order ใหม่ทุกตัวต้องมี broker-side TP/SL

# จุดนี้ทำให้ทั้ง PAPER/LIVE/SIMULATOR ใช้กฎเดียวกัน:

# ไม่มี TP/SL = ไม่ส่ง order

REQUIRE_BROKER_SIDE_TP_SL_FOR_ENTRY = True

PROFIT_LIFECYCLE_EXIT_TYPE = "profit_lifecycle_exit"

def _as_positive_integral(value: Any, field_name: str) -> int:

    try:

        amount = Decimal(str(value))

    except (InvalidOperation, TypeError, ValueError) as exc:

        raise ProtectiveOrderError(

            f"{field_name} must be a positive whole number"

        ) from exc

    if (

        not amount.is_finite()

        or amount <= 0

        or amount != amount.to_integral_value()

    ):

        raise ProtectiveOrderError(

            f"{field_name} must be a positive whole number"

        )

    return int(amount)

def _as_float(value: Any, field_name: str) -> float:

    try:

        result = float(value)

    except (TypeError, ValueError) as exc:

        raise ProtectiveOrderError(f"{field_name} must be a number") from exc

    if result <= 0:

        raise ProtectiveOrderError(f"{field_name} must be greater than zero")

    return result

def alpaca_price(value: Any, field_name: str = "price") -> str:

    """

    Return Alpaca-compatible price text.

    Alpaca rejects sub-penny increments for most US equity orders.

    - Prices >= $1 use max 2 decimal places.

    - Prices < $1 use max 4 decimal places.

    Uses ROUND_HALF_UP to avoid broker rejection from invalid decimal precision.

    """

    amount = Decimal(str(_as_float(value, field_name)))

    quantum = Decimal("0.01") if amount >= Decimal("1") else Decimal("0.0001")

    rounded = amount.quantize(quantum, rounding=ROUND_HALF_UP)

    return format(rounded.normalize(), "f")

def _normalize_side(value: Any) -> str:

    return str(value or "").strip().lower()

def _normalize_symbol(value: Any) -> str:

    return str(value or "").strip().upper()

def _first_present(*values: Any) -> Any:

    for value in values:

        if value is not None and value != "":

            return value

    return None

def expected_exit_side(order: Order) -> str:

    """

    For a BUY entry, protective exit must be SELL.

    For a SELL entry, protective exit must be BUY.

    """

    return OrderSide.SELL.value if order.side == OrderSide.BUY else OrderSide.BUY.value

def protection_plan(order: Order) -> Optional[Dict[str, Any]]:

    """

    Read protective plan from order.

    Priority:

    1. order.protective_exit

    2. order.guard_plan

    """

    plan = order.protective_exit or order.guard_plan

    return dict(plan) if isinstance(plan, dict) else None


def is_profit_lifecycle_exit(order: Order) -> bool:

    """Return whether an order claims the Manager-owned profit-exit contract."""

    plan = order.protective_exit

    if not isinstance(plan, dict):

        return False

    declared_type = str(plan.get("type") or "").strip().lower()

    return (

        declared_type == PROFIT_LIFECYCLE_EXIT_TYPE

        or "reduce_only_intent" in plan

    )


def validate_profit_lifecycle_exit(order: Order) -> Dict[str, Any]:

    """Validate the narrow reduce-only contract before bypassing entry protection."""

    plan = order.protective_exit

    if not isinstance(plan, dict) or plan.get("reduce_only_intent") is not True:

        raise ProtectiveOrderError(

            "reduce-only profit exit requires protective_exit.reduce_only_intent=true"

        )

    if str(plan.get("type") or "").strip().lower() != PROFIT_LIFECYCLE_EXIT_TYPE:

        raise ProtectiveOrderError(

            f"reduce-only profit exit type must be {PROFIT_LIFECYCLE_EXIT_TYPE}"

        )

    if order.side != OrderSide.SELL:

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit must use side=sell"

        )

    decision_id = str(plan.get("decision_id") or "").strip()

    if not decision_id:

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit requires decision_id"

        )

    if decision_id != str(order.trade_id):

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit decision_id must match trade_id"

        )

    symbol = _normalize_symbol(plan.get("symbol") or order.symbol)

    if symbol != _normalize_symbol(order.symbol):

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit symbol must match order symbol"

        )

    quantity = _as_positive_integral(

        plan.get("quantity", order.quantity),

        "reduce-only quantity",

    )

    if quantity != int(order.quantity):

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit quantity must match order quantity"

        )

    metadata = order.metadata if isinstance(order.metadata, dict) else {}

    if str(metadata.get("profit_decision_id") or "").strip() != decision_id:

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit metadata.profit_decision_id "

            "must match decision_id"

        )

    position_id = str(metadata.get("position_id") or "").strip()

    if not position_id:

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit requires metadata.position_id"

        )

    position_version = _as_positive_integral(

        metadata.get("position_version"),

        "metadata.position_version",

    )

    correlation_id = str(metadata.get("correlation_id") or "").strip()

    if not correlation_id:

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit requires metadata.correlation_id"

        )

    if str(metadata.get("advisory_source") or "").strip() != "profit-agent":

        raise ProtectiveOrderError(

            "reduce-only profit lifecycle exit metadata.advisory_source "

            "must be profit-agent"

        )

    return {

        "type": PROFIT_LIFECYCLE_EXIT_TYPE,

        "reduce_only_intent": True,

        "decision_id": decision_id,

        "symbol": symbol,

        "side": order.side.value,

        "quantity": quantity,

        "position_id": position_id,

        "position_version": position_version,

        "correlation_id": correlation_id,

    }


def build_alpaca_reduce_only_exit_payload(order: Order) -> Dict[str, Any]:

    """Build parameters for Alpaca's position-closing endpoint."""

    contract = validate_profit_lifecycle_exit(order)

    if order.order_type != OrderType.MARKET:

        raise ProtectiveOrderError(

            "Alpaca reduce-only profit exit supports market orders only"

        )

    return {

        "symbol_or_asset_id": contract["symbol"],

        "qty": str(contract["quantity"]),

    }

def validate_protection_plan(

    order: Order,

    *,

    required: bool = False,

    require_take_profit: bool = False,

) -> Optional[Dict[str, Any]]:

    """

    Return a normalized protective plan or raise when required/invalid.

    Safety rules:

    - Protective plan must match entry symbol.

    - Protective side must be opposite of entry side.

    - Protective quantity must match entry quantity.

    - Stop loss must exist.

    - Take profit is required when require_take_profit=True.

    - When entry price exists:

      - BUY entry: stop < entry price, take profit > entry price

      - SELL entry: stop > entry price, take profit < entry price

    """

    plan = protection_plan(order)

    if not plan:

        if required or require_take_profit:

            raise ProtectiveOrderError("guard_plan or protective_exit is required")

        return None

    symbol = _normalize_symbol(plan.get("symbol") or order.symbol)

    order_symbol = _normalize_symbol(order.symbol)

    if symbol != order_symbol:

        raise ProtectiveOrderError("protective symbol must match entry order symbol")

    side = _normalize_side(plan.get("side"))

    expected_side = expected_exit_side(order)

    if side != expected_side:

        raise ProtectiveOrderError(

            f"protective side must be {expected_side} for {order.side.value} entries"

        )

    quantity = int(_as_float(plan.get("quantity", order.quantity), "protective quantity"))

    if quantity != int(order.quantity):

        raise ProtectiveOrderError("protective quantity must match entry order quantity")

    trigger_price = _as_float(

        _first_present(

            plan.get("trigger_price"),

            plan.get("stop_price"),

            plan.get("stop_loss_price"),

            plan.get("protection_price"),

        ),

        "protective trigger_price",

    )

    take_profit_price = _first_present(

        plan.get("take_profit_price"),

        plan.get("take_profit"),

        plan.get("target_price"),

        plan.get("limit_price"),

    )

    if require_take_profit and take_profit_price is None:

        raise ProtectiveOrderError(

            "take_profit_price is required for broker-side bracket protection"

        )

    take_profit_value = (

        _as_float(take_profit_price, "take_profit_price")

        if take_profit_price is not None

        else None

    )

    entry_price = order.price

    if entry_price:

        entry_price_value = _as_float(entry_price, "entry price")

        if order.side == OrderSide.BUY:

            if trigger_price >= entry_price_value:

                raise ProtectiveOrderError(

                    "buy entry stop price must be below the entry price"

                )

            if take_profit_value is not None and take_profit_value <= entry_price_value:

                raise ProtectiveOrderError(

                    "buy entry take_profit_price must be above the entry price"

                )

        if order.side == OrderSide.SELL:

            if trigger_price <= entry_price_value:

                raise ProtectiveOrderError(

                    "sell entry stop price must be above the entry price"

                )

            if take_profit_value is not None and take_profit_value >= entry_price_value:

                raise ProtectiveOrderError(

                    "sell entry take_profit_price must be below the entry price"

                )

    time_in_force = str(

        plan.get("time_in_force") or order.time_in_force.value

    ).strip().lower()

    return {

        "symbol": symbol,

        "side": side,

        "quantity": quantity,

        "trigger_price": trigger_price,

        "take_profit_price": take_profit_value,

        "time_in_force": time_in_force,

        "source": plan.get("source") or "risk_guard_plan",

    }

def build_alpaca_entry_payload(

    order: Order,

    *,

    require_protection: bool = False,

    require_bracket: bool = False,

) -> Dict[str, Any]:

    """

    Build an Alpaca entry order payload with broker-side TP/SL protection.

    Important safety behavior:

    - New entry orders are forced to use broker-side bracket protection.

    - This means every new stock order must have both:

      1. stop_loss

      2. take_profit

    - If TP/SL is missing or invalid, this function raises ProtectiveOrderError.

    """

    effective_require_protection = (

        REQUIRE_BROKER_SIDE_TP_SL_FOR_ENTRY or require_protection or require_bracket

    )

    effective_require_bracket = (

        REQUIRE_BROKER_SIDE_TP_SL_FOR_ENTRY or require_bracket

    )

    payload: Dict[str, Any] = {

        "side": order.side.value,

        "symbol": order.symbol,

        "qty": str(order.quantity),

        "type": order.order_type.value,

        "time_in_force": order.time_in_force.value.lower(),

    }

    if order.order_type == OrderType.LIMIT and order.price:

        payload["limit_price"] = alpaca_price(order.price, "limit_price")

    plan = validate_protection_plan(

        order,

        required=effective_require_protection,

        require_take_profit=effective_require_bracket,

    )

    if not plan:

        return payload

    take_profit_price = plan.get("take_profit_price")

    if effective_require_bracket and take_profit_price is None:

        raise ProtectiveOrderError(

            "take_profit_price is required for protected bracket entry orders"

        )

    payload["order_class"] = "bracket"

    payload["stop_loss"] = {

        "stop_price": alpaca_price(

            plan["trigger_price"],

            "stop_loss.stop_price",

        )

    }

    payload["take_profit"] = {

        "limit_price": alpaca_price(

            take_profit_price,

            "take_profit.limit_price",

        )

    }

    return payload
