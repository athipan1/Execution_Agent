import pytest

from app.models import Order, OrderSide, OrderType, TimeInForce
from app.services.protective_order_service import (
    ProtectiveOrderError,
    build_alpaca_entry_payload,
    validate_protection_plan,
)


def order(**overrides):
    data = {
        "order_id": 1,
        "trade_id": "trade-1",
        "account_id": 1,
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 10,
        "time_in_force": TimeInForce.GTC,
        "guard_plan": {"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90},
    }
    data.update(overrides)
    return Order(**data)


def test_validate_guard_plan_normalizes_protective_exit():
    result = validate_protection_plan(order())

    assert result == {
        "symbol": "AAPL",
        "side": "sell",
        "quantity": 10,
        "trigger_price": 90.0,
        "take_profit_price": None,
        "time_in_force": "gtc",
        "source": "risk_guard_plan",
    }


def test_rejects_wrong_protective_side():
    protected_order = order(guard_plan={"symbol": "AAPL", "side": "buy", "quantity": 10, "trigger_price": 90})

    with pytest.raises(ProtectiveOrderError, match="protective side must be sell"):
        validate_protection_plan(protected_order, required=True)


def test_rejects_quantity_mismatch():
    protected_order = order(guard_plan={"symbol": "AAPL", "side": "sell", "quantity": 9, "trigger_price": 90})

    with pytest.raises(ProtectiveOrderError, match="quantity"):
        validate_protection_plan(protected_order, required=True)


def test_builds_oto_stop_loss_payload_for_stop_only_plan_when_bracket_not_required():
    payload = build_alpaca_entry_payload(order(), require_protection=True)

    assert payload["symbol"] == "AAPL"
    assert payload["side"] == "buy"
    assert payload["qty"] == "10"
    assert payload["type"] == "market"
    assert payload["order_class"] == "oto"
    assert payload["stop_loss"] == {"stop_price": "90.0"}


def test_builds_bracket_payload_when_take_profit_is_supplied():
    protected_order = order(
        guard_plan={"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90, "take_profit_price": 120}
    )

    payload = build_alpaca_entry_payload(protected_order, require_protection=True)

    assert payload["order_class"] == "bracket"
    assert payload["stop_loss"] == {"stop_price": "90.0"}
    assert payload["take_profit"] == {"limit_price": "120.0"}


def test_requires_guard_plan_when_configured():
    unprotected_order = order(guard_plan=None, protective_exit=None)

    with pytest.raises(ProtectiveOrderError, match="guard_plan"):
        build_alpaca_entry_payload(unprotected_order, require_protection=True)


def test_requires_take_profit_when_bracket_is_required():
    with pytest.raises(ProtectiveOrderError, match="take_profit_price"):
        build_alpaca_entry_payload(order(), require_bracket=True)


def test_builds_bracket_payload_when_bracket_is_required_and_take_profit_exists():
    protected_order = order(
        guard_plan={"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90, "take_profit_price": 120}
    )

    payload = build_alpaca_entry_payload(protected_order, require_bracket=True)

    assert payload["order_class"] == "bracket"
    assert payload["stop_loss"] == {"stop_price": "90.0"}
    assert payload["take_profit"] == {"limit_price": "120.0"}


def test_rejects_bad_take_profit_direction_when_reference_price_exists():
    protected_order = order(
        price=100,
        guard_plan={"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90, "take_profit_price": 99},
    )

    with pytest.raises(ProtectiveOrderError, match="take_profit_price must be above"):
        build_alpaca_entry_payload(protected_order, require_bracket=True)
