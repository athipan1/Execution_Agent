import pytest
from pydantic import ValidationError

from app.models import OrderSide, OrderType, TradePlanExecutionRequest


def trade_plan_payload(**overrides):
    payload = {
        "plan_id": "plan-1",
        "correlation_id": "corr-1",
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
            "risk_per_share": 5,
            "position_value": 500,
            "position_pct": 0.05,
            "reward_risk_ratio": 2.0,
        },
        "exit": {
            "stop_loss": 95,
            "take_profit": 110,
        },
        "risk_approval_id": "risk-1",
        "manual_approval_required": False,
        "dry_run": False,
        "reasons": [],
        "guard_plan": {"source": "risk_agent", "trigger_price": 95},
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def test_trade_plan_execution_request_converts_to_create_order_request():
    plan = TradePlanExecutionRequest.model_validate(trade_plan_payload())

    order = plan.to_order_request()

    assert order.trade_id == "plan-1"
    assert order.account_id == "1"
    assert order.symbol == "AAPL"
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.MARKET
    assert order.price == 100.0
    assert order.quantity == 5
    assert order.final_quantity == 5
    assert order.risk_approval_id == "risk-1"
    assert order.strategy_bucket == "value_rebound"
    assert order.guard_plan == {"source": "risk_agent", "trigger_price": 95}
    assert order.protective_exit["stop_loss"] == 95
    assert order.protective_exit["take_profit"] == 110


def test_trade_plan_execution_request_rejects_missing_risk_approval():
    payload = trade_plan_payload(risk_approval_id="")

    with pytest.raises(ValidationError, match="risk_approval_id is required"):
        TradePlanExecutionRequest.model_validate(payload)


def test_trade_plan_execution_request_rejects_bad_buy_stop_loss_direction():
    payload = trade_plan_payload(exit={"stop_loss": 101})

    with pytest.raises(ValidationError, match="buy trade stop_loss"):
        TradePlanExecutionRequest.model_validate(payload)


def test_trade_plan_execution_request_requires_limit_price_for_limit_order():
    payload = trade_plan_payload(order_type="limit", limit_price=None)

    with pytest.raises(ValidationError, match="limit_price is required"):
        TradePlanExecutionRequest.model_validate(payload)
