from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.main import create_order, get_broker_adapter
from app.models import CreateOrderRequest, Order, OrderSide, OrderType, OrderStatus


def base_request():
    return CreateOrderRequest(
        trade_id="trade-hardening-test",
        account_id=1,
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        risk_approval_id="approval-1",
        final_quantity=10,
        guard_plan={"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90},
    )


def pending_order():
    return Order(
        order_id=123,
        trade_id="trade-hardening-test",
        account_id=1,
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        status=OrderStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_execute_awaits_lifecycle_without_background_tasks():
    service = Mock()
    service.create_order = AsyncMock(return_value=pending_order())
    executed = pending_order().model_copy(update={"status": OrderStatus.EXECUTED, "executed_quantity": 10})
    service.start_order_execution = AsyncMock(return_value=executed)

    response = await create_order(base_request(), service=service, idempotency_key=None)

    service.start_order_execution.assert_awaited_once()
    assert response.data.status == OrderStatus.EXECUTED
    assert response.data.executed_quantity == 10


def test_live_mode_rejects_simulator_broker():
    with (
        patch("app.main.settings.TRADING_MODE", "LIVE"),
        patch("app.main.settings.ALLOW_LIVE_TRADING", True),
        patch("app.main.settings.BROKER_MODE", "SIMULATOR"),
    ):
        with pytest.raises(RuntimeError, match="BROKER_MODE=ALPACA"):
            get_broker_adapter()


def test_unknown_broker_mode_rejected():
    with (
        patch("app.main.settings.TRADING_MODE", "PAPER"),
        patch("app.main.settings.BROKER_MODE", "UNKNOWN"),
    ):
        with pytest.raises(RuntimeError, match="Unsupported BROKER_MODE"):
            get_broker_adapter()
