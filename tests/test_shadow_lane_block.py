import pytest
from fastapi import HTTPException

from app.models import TradePlanExecutionRequest
from app.trade_plan_execution import _ensure_not_shadow_execution


def _trade_plan(metadata):
    return TradePlanExecutionRequest.model_validate(
        {
            "plan_id": "shadow-plan-1",
            "correlation_id": "corr-shadow-execution",
            "source": "scanner",
            "status": "risk_approved",
            "account_id": "1",
            "symbol": "NVDA",
            "side": "buy",
            "order_type": "market",
            "entry_price": 180.0,
            "quantity": 1,
            "final_quantity": 1,
            "time_in_force": "GTC",
            "strategy": "trend_following",
            "strategy_bucket": "value_rebound",
            "final_verdict": "buy",
            "confidence_score": 0.7,
            "expected_r": 2.0,
            "risk": {
                "account_equity": 10000,
                "max_loss_amount": 5,
                "max_loss_pct": 0.0005,
                "risk_per_share": 5,
                "position_value": 180,
                "position_pct": 0.018,
                "reward_risk_ratio": 2.0,
            },
            "exit": {"stop_loss": 175.0, "take_profit": 190.0},
            "risk_approval_id": "should-never-be-used",
            "manual_approval_required": True,
            "dry_run": True,
            "guard_plan": {"trigger_price": 175.0},
            "metadata": metadata,
        }
    )


def test_execution_hard_rejects_shadow_execution_mode():
    plan = _trade_plan(
        {
            "execution_mode": "shadow",
            "lane": "research",
            "broker_order_authorized": False,
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        _ensure_not_shadow_execution(plan)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "shadow_lane_cannot_execute_broker_orders"


def test_execution_hard_rejects_shadow_lane_alias():
    plan = _trade_plan({"lane": "shadow"})

    with pytest.raises(HTTPException) as exc_info:
        _ensure_not_shadow_execution(plan)

    assert exc_info.value.status_code == 403


def test_execution_allows_normal_production_plan_to_continue_to_existing_gates():
    plan = _trade_plan({"execution_mode": "production", "lane": "production"})

    assert _ensure_not_shadow_execution(plan) is None
