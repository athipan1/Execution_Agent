import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock

from app.adapters.alpaca import AlpacaAdapter
from app.models import Order, OrderSide, OrderType, TimeInForce, OrderStatus
from app.config import settings

# Configure Alpaca settings for tests
settings.ALPACA_API_KEY_ID = "test_api_key_id"
settings.ALPACA_SECRET_KEY = "test_secret_key"


@pytest.fixture
def alpaca_adapter():
    """Provides a clean instance of the AlpacaAdapter for each test."""
    return AlpacaAdapter()


@pytest.fixture
def sample_order():
    return Order(
        order_id=1,
        trade_id="test-order-123",
        account_id=100,
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        time_in_force=TimeInForce.GTC,
    )


@pytest.mark.asyncio
@respx.mock
async def test_check_connection_success(alpaca_adapter):
    """
    Tests that check_connection returns True when the API key is valid.
    """
    account_request = respx.get(f"{settings.ALPACA_API_URL}/v2/account").mock(
        return_value=Response(200, json={"id": "test_account"}),
    )
    assert await alpaca_adapter.check_connection() is True
    assert account_request.called
    assert account_request.calls.last.request.headers["APCA-API-KEY-ID"] == "test_api_key_id"
    assert account_request.calls.last.request.headers["APCA-API-SECRET-KEY"] == "test_secret_key"


@pytest.mark.asyncio
@respx.mock
async def test_check_connection_failure(alpaca_adapter):
    """
    Tests that check_connection returns False when the API returns an error.
    """
    respx.get(f"{settings.ALPACA_API_URL}/v2/account").mock(
        return_value=Response(401, text="Unauthorized")
    )
    assert await alpaca_adapter.check_connection() is False


@pytest.mark.asyncio
@respx.mock
async def test_place_order_success(alpaca_adapter, sample_order):
    """
    Tests a successful order placement workflow with API Key authentication.
    """
    order_request = respx.post(f"{settings.ALPACA_API_URL}/v2/orders").mock(
        return_value=Response(200, json={"id": "broker-order-id-123", "status": "accepted"})
    )

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    assert order_request.called
    assert order_request.calls.last.request.headers["APCA-API-KEY-ID"] == "test_api_key_id"
    update_callback.assert_awaited_once_with({
        "order_id": sample_order.order_id,
        "status": OrderStatus.PLACED,
        "broker_order_id": "broker-order-id-123",
    })


@pytest.mark.asyncio
@respx.mock
async def test_place_order_api_failure(alpaca_adapter, sample_order):
    """
    Tests the workflow where the Alpaca API returns an error during order placement.
    """
    respx.post(f"{settings.ALPACA_API_URL}/v2/orders").mock(
        return_value=Response(403, json={"message": "Insufficient buying power"})
    )

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    update_callback.assert_awaited_once_with({
        "order_id": sample_order.order_id,
        "status": OrderStatus.FAILED,
        "reason": 'Broker API request failed with status 403: {"message":"Insufficient buying power"}',
    })
