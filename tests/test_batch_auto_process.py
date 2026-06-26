from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.main import create_order_batch
from app.models import (
    CreateOrderRequest,
    ExecutionJob,
    ExecutionJobStatus,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)


def order_request():
    return CreateOrderRequest(
        trade_id="portfolio-ADBE-1",
        account_id=1,
        symbol="ADBE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=2,
        time_in_force=TimeInForce.GTC,
        strategy_bucket="core_dividend",
        risk_approval_id="risk-adbe",
        final_quantity=2,
        guard_plan={"symbol": "ADBE", "side": "sell", "quantity": 2, "trigger_price": 400},
    )


def pending_order():
    return Order(
        order_id=101,
        trade_id="portfolio-ADBE-1",
        account_id=1,
        symbol="ADBE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=2,
        time_in_force=TimeInForce.GTC,
        strategy_bucket="core_dividend",
        status=OrderStatus.PENDING,
    )


def placed_order():
    return pending_order().model_copy(
        update={"status": OrderStatus.PLACED, "broker_order_id": "alpaca-order-1"}
    )


def failed_order():
    return pending_order().model_copy(
        update={"status": OrderStatus.FAILED, "reason": "Alpaca rejected: insufficient buying power"}
    )


@pytest.mark.asyncio
async def test_batch_auto_processes_in_paper_and_returns_order_diagnostics():
    service = Mock()
    service.create_order = AsyncMock(return_value=pending_order())
    service.enqueue_order_execution = AsyncMock(
        return_value=ExecutionJob(job_id=7, order_id=101, trade_id="portfolio-ADBE-1")
    )
    service.start_order_execution = AsyncMock(return_value=placed_order())
    service.db_client = Mock()
    service.db_client.update_execution_job = AsyncMock(
        return_value=ExecutionJob(
            job_id=7,
            order_id=101,
            trade_id="portfolio-ADBE-1",
            status=ExecutionJobStatus.SUCCEEDED,
        )
    )
    adapter = Mock()
    adapter.get_open_orders = AsyncMock(return_value=[])

    with patch("app.main.settings.TRADING_ENABLED", True), patch("app.main.settings.TRADING_MODE", "PAPER"):
        response = await create_order_batch([order_request()], service=service, adapter=adapter)

    created = response.data["created"][0]
    assert response.data["approved"] is True
    assert response.data["auto_process"] is True
    assert response.data["failed"] == []
    assert created["symbol"] == "ADBE"
    assert created["quantity"] == 2
    assert created["final_quantity"] == 2
    assert created["risk_approval_id"] == "risk-adbe"
    assert created["order_id"] == 101
    assert created["broker_order_id"] == "alpaca-order-1"
    assert created["processed_now"] is True
    assert created["execution_job"]["status"] == "succeeded"
    assert "failure_detail" not in created


@pytest.mark.asyncio
async def test_batch_auto_process_failure_returns_failure_diagnostics():
    service = Mock()
    service.create_order = AsyncMock(return_value=pending_order())
    service.enqueue_order_execution = AsyncMock(
        return_value=ExecutionJob(job_id=7, order_id=101, trade_id="portfolio-ADBE-1")
    )
    service.start_order_execution = AsyncMock(return_value=failed_order())
    service.db_client = Mock()
    service.db_client.update_execution_job = AsyncMock(
        return_value=ExecutionJob(
            job_id=7,
            order_id=101,
            trade_id="portfolio-ADBE-1",
            status=ExecutionJobStatus.FAILED,
            last_error="Alpaca rejected: insufficient buying power",
        )
    )
    adapter = Mock()
    adapter.get_open_orders = AsyncMock(return_value=[])

    with patch("app.main.settings.TRADING_ENABLED", True), patch("app.main.settings.TRADING_MODE", "PAPER"), patch("app.main.settings.BROKER_MODE", "ALPACA"):
        response = await create_order_batch([order_request()], service=service, adapter=adapter)

    assert response.data["approved"] is False
    assert response.data["created"] == []
    failed = response.data["failed"][0]
    assert failed["symbol"] == "ADBE"
    assert failed["status"] == "failed"
    assert failed["reason"] == "Alpaca rejected: insufficient buying power"
    assert failed["execution_job"]["status"] == "failed"
    assert failed["execution_job"]["last_error"] == "Alpaca rejected: insufficient buying power"
    assert failed["failure_detail"] == {
        "failed_step": "broker_submission",
        "reason": "Alpaca rejected: insufficient buying power",
        "broker_mode": "ALPACA",
        "trading_mode": "PAPER",
        "order_status": "failed",
        "job_status": "failed",
        "job_last_error": "Alpaca rejected: insufficient buying power",
        "broker_order_id": None,
        "order_request": {
            "trade_id": "portfolio-ADBE-1",
            "account_id": 1,
            "symbol": "ADBE",
            "side": "buy",
            "order_type": "market",
            "price": None,
            "quantity": 2,
            "final_quantity": 2,
            "time_in_force": "GTC",
            "strategy_bucket": "core_dividend",
            "risk_approval_id": "risk-adbe",
            "has_guard_plan": True,
            "has_protective_exit": False,
            "guard_plan": {"symbol": "ADBE", "side": "sell", "quantity": 2, "trigger_price": 400},
            "protective_exit": None,
        },
        "diagnostic_hint": "Inspect broker response in reason/job_last_error. If broker_order_id is missing, the broker rejected or never accepted the order.",
    }


@pytest.mark.asyncio
async def test_batch_does_not_auto_process_when_disabled_by_query_param():
    service = Mock()
    service.create_order = AsyncMock(return_value=pending_order())
    service.enqueue_order_execution = AsyncMock(
        return_value=ExecutionJob(job_id=8, order_id=101, trade_id="portfolio-ADBE-1")
    )
    service.start_order_execution = AsyncMock()
    adapter = Mock()
    adapter.get_open_orders = AsyncMock(return_value=[])

    with patch("app.main.settings.TRADING_ENABLED", True), patch("app.main.settings.TRADING_MODE", "PAPER"):
        response = await create_order_batch([order_request()], auto_process=False, service=service, adapter=adapter)

    service.start_order_execution.assert_not_called()
    created = response.data["created"][0]
    assert response.data["auto_process"] is False
    assert created["processed_now"] is False
    assert created["status"] == "pending"
    assert response.data["failed"] == []
