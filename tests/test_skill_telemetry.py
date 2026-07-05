from datetime import datetime, timezone

import pytest

from app.db_client import InMemoryDatabaseClient
from app.models import (
    CreateOrderRequest,
    FillPayload,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioExecutionRequest,
    PortfolioRiskApproval,
    TimeInForce,
    TradePlanExecutionRequest,
    TradePlanRiskEnvelope,
)
from app.services.portfolio_execution import build_order_requests_from_portfolio
from app.services.skill_telemetry import build_skill_trade_outcome_payload, extract_skill_metadata


def curator_metadata():
    return {
        "curator_signal": {
            "skill_id": "skill-1",
            "selected_skill": {"skill_id": "skill-1", "score": 0.82},
            "execution": {
                "output": {"signal": "buy", "confidence": 0.74},
                "database_telemetry": {"execution_log_id": "exec-log-1"},
            },
        }
    }


def test_trade_plan_to_order_request_carries_metadata():
    trade_plan = TradePlanExecutionRequest(
        plan_id="plan-1",
        correlation_id="corr-1",
        account_id="1",
        symbol="acgl",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=100.0,
        quantity=10,
        final_quantity=10,
        strategy_bucket="value_rebound",
        final_verdict="buy",
        confidence_score=0.7,
        risk=TradePlanRiskEnvelope(max_loss_amount=50.0, max_loss_pct=0.01),
        risk_approval_id="risk-1",
        guard_plan={"stop_loss": 95.0},
        metadata=curator_metadata(),
    )

    order_request = trade_plan.to_order_request()

    assert order_request.symbol == "ACGL"
    assert order_request.metadata["curator_signal"]["skill_id"] == "skill-1"


def test_portfolio_execution_carries_approval_metadata():
    request = PortfolioExecutionRequest(
        account_id="1",
        approvals=[
            PortfolioRiskApproval(
                symbol="acgl",
                approved=True,
                strategy_bucket="value_rebound",
                risk_approval_id="risk-1",
                final_quantity=10,
                guard_plan={"stop_loss": 95.0},
                metadata=curator_metadata(),
            )
        ],
        default_price=100.0,
    )

    orders, failed = build_order_requests_from_portfolio(request)

    assert failed == []
    assert len(orders) == 1
    assert orders[0].symbol == "ACGL"
    assert orders[0].metadata["curator_signal"]["skill_id"] == "skill-1"


def test_portfolio_execution_merges_risk_response_and_approval_metadata():
    request = PortfolioExecutionRequest(
        account_id="1",
        approvals=[
            PortfolioRiskApproval(
                symbol="ACGL",
                approved=True,
                strategy_bucket="value_rebound",
                risk_approval_id="risk-1",
                final_quantity=10,
                guard_plan={"stop_loss": 95.0},
                risk_response={"metadata": {"source": "risk", "keep": True}},
                metadata={"source": "approval", **curator_metadata()},
            )
        ],
        default_price=100.0,
    )

    orders, failed = build_order_requests_from_portfolio(request)

    assert failed == []
    assert orders[0].metadata["source"] == "approval"
    assert orders[0].metadata["keep"] is True
    assert orders[0].metadata["curator_signal"]["skill_id"] == "skill-1"


def test_extract_skill_metadata_from_curator_signal():
    extracted = extract_skill_metadata(curator_metadata())

    assert extracted["skill_id"] == "skill-1"
    assert extracted["execution_log_id"] == "exec-log-1"
    assert extracted["signal"] == "buy"
    assert extracted["confidence"] == 0.82


def test_build_skill_trade_outcome_payload_from_fill():
    order = Order(
        order_id=1,
        trade_id="trade-1",
        account_id="1",
        symbol="ACGL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        price=100.0,
        quantity=10,
        time_in_force=TimeInForce.GTC,
        strategy_bucket="value_rebound",
        status=OrderStatus.EXECUTED,
        metadata=curator_metadata(),
    )
    fill = FillPayload(
        order_id=1,
        trade_id="trade-1",
        symbol="ACGL",
        side=OrderSide.BUY,
        quantity=10,
        fill_price=104.0,
        realized_pnl=40.0,
        broker_fill_id="fill-1",
        broker_order_id="broker-1",
        filled_at=datetime.now(timezone.utc),
    )

    payload = build_skill_trade_outcome_payload(order=order, fill=fill, realized_pl=fill.realized_pnl)

    assert payload is not None
    assert payload["execution_log_id"] == "exec-log-1"
    assert payload["skill_id"] == "skill-1"
    assert payload["symbol"] == "ACGL"
    assert payload["outcome"] == "win"
    assert payload["metadata"]["broker_fill_id"] == "fill-1"


@pytest.mark.asyncio
async def test_in_memory_database_records_skill_trade_outcome():
    db = InMemoryDatabaseClient()
    payload = {"execution_log_id": "exec-log-1", "skill_id": "skill-1", "symbol": "ACGL"}

    result = await db.record_skill_trade_outcome(payload)

    assert result == payload
    assert db.skill_trade_outcomes == [payload]


def test_create_order_request_accepts_metadata():
    request = CreateOrderRequest(
        trade_id="trade-1",
        account_id="1",
        symbol="ACGL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        final_quantity=10,
        risk_approval_id="risk-1",
        guard_plan={"stop_loss": 95.0},
        metadata=curator_metadata(),
    )

    assert request.metadata["curator_signal"]["skill_id"] == "skill-1"
