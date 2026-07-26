import httpx
import pytest
import respx

from app.db_client import HttpDatabaseClient
from app.models import Order, TimeInForce


def production_order_row(time_in_force: str = "day") -> dict:
    return {
        "order_id": 1,
        "trade_id": "broker:11892a93-bac7-4c22-b11a-043818288cf8",
        "account_id": 1,
        "symbol": "ACGL",
        "side": "sell",
        "order_type": "limit",
        "price": 109.14,
        "quantity": 151,
        "time_in_force": time_in_force,
        "strategy_bucket": "value_rebound",
        "status": "placed",
        "broker_order_id": "11892a93-bac7-4c22-b11a-043818288cf8",
        "reason": None,
        "executed_quantity": 0,
        "avg_execution_price": None,
        "executed_at": None,
        "guard_plan": None,
        "protective_exit": None,
    }


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("day", TimeInForce.DAY),
        ("DAY", TimeInForce.DAY),
        ("gtc", TimeInForce.GTC),
        ("ioc", TimeInForce.IOC),
        ("fok", TimeInForce.FOK),
    ],
)
def test_order_accepts_case_insensitive_database_time_in_force(raw_value, expected):
    order = Order.model_validate(production_order_row(raw_value))

    assert order.time_in_force is expected


@pytest.mark.asyncio
async def test_http_database_client_lists_production_day_order_for_reconciliation():
    base_url = "http://database-agent"
    client = HttpDatabaseClient(base_url)
    response_body = {
        "status": "success",
        "agent_type": "database",
        "version": "1.1.0",
        "data": [production_order_row()],
        "metadata": {},
        "error": None,
    }

    with respx.mock:
        request = respx.get(
            f"{base_url}/orders",
            params={"status": "in_flight", "limit": 100},
        ).mock(return_value=httpx.Response(200, json=response_body))

        orders = await client.list_in_flight_orders(limit=100)

    assert request.called
    assert len(orders) == 1
    assert orders[0].symbol == "ACGL"
    assert orders[0].time_in_force is TimeInForce.DAY
    assert orders[0].broker_order_id == "11892a93-bac7-4c22-b11a-043818288cf8"
