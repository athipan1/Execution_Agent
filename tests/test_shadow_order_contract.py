import pytest
from pydantic import ValidationError

from app.models import CreateOrderRequest


def _order(metadata):
    return {
        "trade_id": "shadow-direct-1",
        "account_id": "1",
        "symbol": "NVDA",
        "side": "buy",
        "order_type": "market",
        "price": 180.0,
        "quantity": 1,
        "time_in_force": "GTC",
        "strategy_bucket": "value_rebound",
        "risk_approval_id": "risk-production-only",
        "final_quantity": 1,
        "guard_plan": {"trigger_price": 175.0},
        "metadata": metadata,
    }


def test_direct_order_contract_rejects_shadow_execution_mode():
    with pytest.raises(ValidationError) as exc_info:
        CreateOrderRequest.model_validate(
            _order({"execution_mode": "shadow", "lane": "research"})
        )

    assert "shadow_lane_cannot_execute_broker_orders" in str(exc_info.value)


def test_direct_order_contract_rejects_shadow_lane():
    with pytest.raises(ValidationError):
        CreateOrderRequest.model_validate(_order({"lane": "shadow"}))


def test_direct_order_contract_keeps_production_path_compatible():
    order = CreateOrderRequest.model_validate(
        _order({"execution_mode": "production", "lane": "production"})
    )

    assert order.symbol == "NVDA"
    assert order.final_quantity == 1
