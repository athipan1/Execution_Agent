from unittest.mock import AsyncMock

import pytest
import respx
from httpx import Response

from app.adapters.alpaca import AlpacaAdapter
from app.adapters.simulator import SimulatorAdapter
from app.config import settings
from app.models import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from app.services.protective_order_service import (
    ProtectiveOrderError,
    build_alpaca_reduce_only_exit_payload,
    validate_profit_lifecycle_exit,
)


DECISION_ID = "profit:account-1:position-42:ACGL:v7:tp1"


def reduce_only_order(**overrides):
    data = {
        "order_id": 42,
        "trade_id": DECISION_ID,
        "account_id": 1,
        "symbol": "ACGL",
        "side": OrderSide.SELL,
        "order_type": OrderType.MARKET,
        "quantity": 3,
        "time_in_force": TimeInForce.GTC,
        "strategy_bucket": "value_rebound",
        "protective_exit": {
            "type": "profit_lifecycle_exit",
            "reduce_only_intent": True,
            "decision_id": DECISION_ID,
        },
        "metadata": {
            "profit_decision_id": DECISION_ID,
            "position_id": "account-1:position-42",
            "position_version": 7,
            "correlation_id": "corr-profit-exit",
        },
    }
    data.update(overrides)
    return Order(**data)


def test_reduce_only_profit_exit_contract_is_narrow_and_deterministic():
    order = reduce_only_order()

    contract = validate_profit_lifecycle_exit(order)
    payload = build_alpaca_reduce_only_exit_payload(order)

    assert contract == {
        "type": "profit_lifecycle_exit",
        "reduce_only_intent": True,
        "decision_id": DECISION_ID,
        "symbol": "ACGL",
        "side": "sell",
        "quantity": 3,
    }
    assert payload == {
        "side": "sell",
        "symbol": "ACGL",
        "qty": "3",
        "type": "market",
        "time_in_force": "gtc",
    }
    assert "order_class" not in payload
    assert "stop_loss" not in payload
    assert "take_profit" not in payload


@pytest.mark.parametrize(
    ("protective_exit", "message"),
    [
        (
            {
                "type": "profit_lifecycle_exit",
                "reduce_only_intent": True,
                "decision_id": "wrong-decision",
            },
            "decision_id must match trade_id",
        ),
        (
            {
                "type": "unexpected",
                "reduce_only_intent": True,
                "decision_id": DECISION_ID,
            },
            "type must be profit_lifecycle_exit",
        ),
    ],
)
def test_invalid_reduce_only_contract_fails_closed(protective_exit, message):
    with pytest.raises(ProtectiveOrderError, match=message):
        validate_profit_lifecycle_exit(
            reduce_only_order(protective_exit=protective_exit)
        )


@pytest.mark.asyncio
async def test_simulator_executes_reduce_only_profit_exit_without_nested_bracket():
    callback = AsyncMock()

    await SimulatorAdapter().place_order(reduce_only_order(), callback)

    assert callback.call_count == 2
    placed = callback.call_args_list[0].args[0]
    executed = callback.call_args_list[1].args[0]
    assert placed["status"] == OrderStatus.PLACED
    assert placed["order_class"] == "reduce_only_exit"
    assert placed["protection_required"] is False
    assert placed["reduce_only_intent"] is True
    assert placed["broker_order_id"].startswith("sim-profit-exit-")
    assert executed["status"] == OrderStatus.EXECUTED
    assert executed["executed_quantity"] == 3


@pytest.mark.asyncio
@respx.mock
async def test_alpaca_submits_plain_sell_for_reduce_only_profit_exit():
    settings.ALPACA_API_KEY_ID = "test_api_key_id"
    settings.ALPACA_SECRET_KEY = "test_secret_key"
    adapter = AlpacaAdapter()
    order_route = respx.post(f"{settings.ALPACA_API_URL}/v2/orders").mock(
        return_value=Response(
            200,
            json={"id": "profit-exit-order-1", "status": "accepted"},
        )
    )
    callback = AsyncMock()

    await adapter.place_order(reduce_only_order(), callback)

    assert order_route.called
    payload = order_route.calls.last.request.content.decode("utf-8")
    assert '"side":"sell"' in payload
    assert '"qty":"3"' in payload
    assert '"order_class"' not in payload
    assert '"stop_loss"' not in payload
    assert '"take_profit"' not in payload
    callback.assert_awaited_once_with(
        {
            "order_id": 42,
            "status": OrderStatus.PLACED,
            "broker_order_id": "profit-exit-order-1",
            "executed_quantity": 0,
            "broker_status": "accepted",
        }
    )
