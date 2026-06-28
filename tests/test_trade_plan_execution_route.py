from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.models import (
    ExecutionJob,
    ExecutionJobStatus,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TradePlanExecutionRequest,
)
from app.trade_plan_execution import create_order_from_trade_plan


def trade_plan_payload(**overrides):
    payload = {
        "plan_id": "plan-route-1",
        "correlation_id": "corr-route-1",
        "source": "single_analysis",
        "status": "risk_approved",
        "account_id": "1",
        "symbol": "aapl",
        "side": "buy",
        "order_type": "market",
        "entry_price": 100.0,
        "quantity": 5,
        "final_quantity": 5,
        "time_in_force": "GTC",
        "strategy": "trend_pullback",
        "strategy_bucket": "value_rebound",
        "final_verdict": "buy",
        "confidence_score": 0.7,
        "expected_r": 2.0,
        "risk": {
            "account_equity": 10000,
            "max_loss_amount": 25,
            "max_loss_pct": 0.0025,
        },
        "exit": {
            "stop_loss": 95,
            "take_profit": 110,
        },
        "risk_approval_id": "risk-route-1",
        "manual_approval_required": False,
        "dry_run": False,
        "reasons": [],
        "guard_plan": {"source": "risk_agent", "trigger_price": 95},
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def pending_order():
    return Order(
        order_id=321,
        trade_id="plan-route-1",
        account_id="1",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        price=100.0,
        quantity=5,
        status=OrderStatus.PENDING,
        guard_plan={"source": "risk_agent", "trigger_price": 95},
        protective_exit={"stop_loss": 95, "take_profit": 110},
    )


@pytest.mark.asyncio
async def test_create_order_from_trade_plan_reuses_execution_service():
    service = Mock()
    service.create_order = AsyncMock(return_value=pending_order())
    service.enqueue_order_execution = AsyncMock(
        return_value=ExecutionJob(
            job_id=7,
            order_id=321,
            trade_id="plan-route-1",
            status=ExecutionJobStatus.QUEUED,
        )
    )
    trade_plan = TradePlanExecutionRequest.model_validate(trade_plan_payload())

    with patch("app.trade_plan_execution.settings.TRADING_ENABLED", True):
        response = await create_order_from_trade_plan(
            trade_plan,
            service=service,
            idempotency_key=None,
        )

    service.create_order.assert_awaited_once()
    submitted_order_request = service.create_order.await_args.args[0]
    assert submitted_order_request.trade_id == "plan-route-1"
    assert submitted_order_request.risk_approval_id == "risk-route-1"
    assert submitted_order_request.strategy_bucket == "value_rebound"
    assert submitted_order_request.protective_exit["stop_loss"] == 95
    assert response.status == "success"
    assert response.data["trade_plan_id"] == "plan-route-1"
    assert response.data["correlation_id"] == "corr-route-1"
    assert response.data["order"]["status"] == "pending"
    assert response.data["execution_job"]["status"] == "queued"


@pytest.mark.asyncio
async def test_create_order_from_trade_plan_honors_idempotency_key():
    service = Mock()
    service.create_order = AsyncMock(return_value=pending_order())
    service.enqueue_order_execution = AsyncMock(
        return_value=ExecutionJob(
            job_id=7,
            order_id=321,
            trade_id="idem-key-1",
            status=ExecutionJobStatus.QUEUED,
        )
    )
    trade_plan = TradePlanExecutionRequest.model_validate(trade_plan_payload())

    with patch("app.trade_plan_execution.settings.TRADING_ENABLED", True):
        await create_order_from_trade_plan(
            trade_plan,
            service=service,
            idempotency_key="idem-key-1",
        )

    submitted_order_request = service.create_order.await_args.args[0]
    assert submitted_order_request.trade_id == "idem-key-1"


@pytest.mark.asyncio
async def test_create_order_from_trade_plan_rejects_when_trading_disabled():
    service = Mock()
    service.create_order = AsyncMock()
    service.enqueue_order_execution = AsyncMock()
    trade_plan = TradePlanExecutionRequest.model_validate(trade_plan_payload())

    with patch("app.trade_plan_execution.settings.TRADING_ENABLED", False):
        with pytest.raises(HTTPException) as exc_info:
            await create_order_from_trade_plan(
                trade_plan,
                service=service,
                idempotency_key=None,
            )

    assert exc_info.value.status_code == 423
    service.create_order.assert_not_called()
    service.enqueue_order_execution.assert_not_called()
