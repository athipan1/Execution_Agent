from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.main import create_order, get_broker_adapter
from app.models import CreateOrderRequest, Order, OrderSide, OrderType, OrderStatus, ExecutionJob, ExecutionJobStatus


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
async def test_execute_rejects_when_trading_disabled():
    service = Mock()
    service.create_order = AsyncMock()
    service.enqueue_order_execution = AsyncMock()

    with patch("app.main.settings.TRADING_ENABLED", False):
        with pytest.raises(HTTPException) as exc_info:
            await create_order(base_request(), service=service, idempotency_key=None)

    assert exc_info.value.status_code == 423
    assert "TRADING_ENABLED=false" in exc_info.value.detail
    service.create_order.assert_not_called()
    service.enqueue_order_execution.assert_not_called()


@pytest.mark.asyncio
async def test_execute_enqueues_job_without_broker_lifecycle():
    service = Mock()
    service.create_order = AsyncMock(return_value=pending_order())
    service.enqueue_order_execution = AsyncMock(return_value=ExecutionJob(job_id=1, order_id=123, trade_id="trade-hardening-test", status=ExecutionJobStatus.QUEUED))
    service.start_order_execution = AsyncMock()

    with patch("app.main.settings.TRADING_ENABLED", True):
        response = await create_order(base_request(), service=service, idempotency_key=None)

    service.enqueue_order_execution.assert_awaited_once()
    service.start_order_execution.assert_not_called()
    assert response.data["order"]["status"] == "pending"
    assert response.data["execution_job"]["status"] == "queued"


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
