import pytest
import respx
import httpx
from app.db_client import HttpDatabaseClient
from app.models import CreateOrderRequest, Order, OrderSide, OrderType, TimeInForce, OrderStatus

@pytest.mark.asyncio
async def test_http_db_client_create_order():
    base_url = "http://db-agent"
    client = HttpDatabaseClient(base_url)

    order_request = CreateOrderRequest(
        client_order_id="test-client-id",
        account_id=1,
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        time_in_force=TimeInForce.GTC
    )

    mock_order_response = {
        "order_id": 123,
        "client_order_id": "test-client-id",
        "account_id": 1,
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "market",
        "quantity": 10,
        "time_in_force": "GTC",
        "status": "pending"
    }

    with respx.mock:
        respx.post(f"{base_url}/orders").mock(return_value=httpx.Response(200, json=mock_order_response))

        order = await client.create_order(order_request)

        assert order.order_id == 123
        assert order.status == OrderStatus.PENDING

@pytest.mark.asyncio
async def test_http_db_client_get_order():
    base_url = "http://db-agent"
    client = HttpDatabaseClient(base_url)

    mock_order_response = {
        "order_id": 123,
        "client_order_id": "test-client-id",
        "account_id": 1,
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "market",
        "quantity": 10,
        "time_in_force": "GTC",
        "status": "executed"
    }

    with respx.mock:
        respx.get(f"{base_url}/orders/123").mock(return_value=httpx.Response(200, json=mock_order_response))
        respx.get(f"{base_url}/orders/456").mock(return_value=httpx.Response(404))

        order = await client.get_order_by_order_id(123)
        assert order is not None
        assert order.status == OrderStatus.EXECUTED

        order_not_found = await client.get_order_by_order_id(456)
        assert order_not_found is None

@pytest.mark.asyncio
async def test_http_db_client_update_order():
    base_url = "http://db-agent"
    client = HttpDatabaseClient(base_url)

    updates = {"status": "executed"}
    mock_order_response = {
        "order_id": 123,
        "client_order_id": "test-client-id",
        "account_id": 1,
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "market",
        "quantity": 10,
        "time_in_force": "GTC",
        "status": "executed"
    }

    with respx.mock:
        respx.patch(f"{base_url}/orders/123").mock(return_value=httpx.Response(200, json=mock_order_response))

        order = await client.update_order(123, updates)
        assert order.status == OrderStatus.EXECUTED
