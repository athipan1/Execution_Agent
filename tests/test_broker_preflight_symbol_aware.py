from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from app.services.broker_preflight import BrokerPreflightError, validate_broker_preflight


def _order(symbol="ACGL"):
    return Order(
        order_id=2,
        trade_id=f"trade-{symbol}",
        account_id=1,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        price=100,
        time_in_force=TimeInForce.GTC,
        status=OrderStatus.PENDING,
    )


def _account():
    return {
        "broker": "ALPACA",
        "paper": True,
        "status": "ACTIVE",
        "cash": "93276.77",
        "buying_power": "402363.94",
        "equity": "103725.65",
        "portfolio_value": "103725.65",
        "trading_blocked": False,
        "transfers_blocked": False,
        "account_blocked": False,
    }


def _stale_order(symbol="ADBE"):
    return {
        "id": "stale-order-1",
        "symbol": symbol,
        "side": "sell",
        "qty": "52",
        "type": "stop",
        "time_in_force": "gtc",
        "status": "new",
        "submitted_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "stop_price": "190.12",
    }


def test_other_symbol_stale_open_order_warns_but_does_not_block(monkeypatch):
    monkeypatch.setattr(settings, "FAIL_ON_STALE_OPEN_ORDERS", True)
    monkeypatch.setattr(settings, "MAX_STALE_OPEN_ORDER_AGE_MINUTES", 390)

    snapshot = validate_broker_preflight(
        account=_account(),
        positions=[],
        open_orders=[_stale_order("ADBE")],
        order=_order("ACGL"),
    )

    assert snapshot["approved"] is True
    assert snapshot["violations"] == []
    assert snapshot["warnings"] == ["other_symbol_stale_open_orders_present"]
    assert snapshot["stale_open_order_count"] == 1
    assert snapshot["same_symbol_stale_open_order_count"] == 0
    assert snapshot["other_symbol_stale_open_order_count"] == 1
    assert snapshot["other_symbol_stale_open_orders"][0]["symbol"] == "ADBE"


def test_same_symbol_stale_open_order_still_blocks(monkeypatch):
    monkeypatch.setattr(settings, "FAIL_ON_STALE_OPEN_ORDERS", True)
    monkeypatch.setattr(settings, "MAX_STALE_OPEN_ORDER_AGE_MINUTES", 390)

    with pytest.raises(BrokerPreflightError) as exc:
        validate_broker_preflight(
            account=_account(),
            positions=[],
            open_orders=[_stale_order("ACGL")],
            order=_order("ACGL"),
        )

    assert "same_symbol_stale_open_orders_present" in str(exc.value)


def test_same_symbol_stale_open_order_can_warn_when_fail_closed_disabled(monkeypatch):
    monkeypatch.setattr(settings, "FAIL_ON_STALE_OPEN_ORDERS", False)
    monkeypatch.setattr(settings, "MAX_STALE_OPEN_ORDER_AGE_MINUTES", 390)

    snapshot = validate_broker_preflight(
        account=_account(),
        positions=[],
        open_orders=[_stale_order("ACGL")],
        order=_order("ACGL"),
    )

    assert snapshot["approved"] is True
    assert snapshot["violations"] == []
    assert snapshot["warnings"] == ["same_symbol_stale_open_orders_present"]
    assert snapshot["same_symbol_stale_open_order_count"] == 1
