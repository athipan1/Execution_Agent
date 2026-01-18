import pytest
import respx
from httpx import Response
from unittest.mock import AsyncMock
from datetime import datetime, timedelta, timezone

from app.adapters.alpaca import AlpacaAdapter
from app.models import Order, OrderSide, OrderType, TimeInForce, OrderStatus
from app.config import settings

# Configure Alpaca settings for tests
settings.ALPACA_CLIENT_ID = "test_client_id"
settings.ALPACA_CLIENT_SECRET = "test_client_secret"

@pytest.fixture
def alpaca_adapter(monkeypatch):
    """
    Provides a clean instance of the AlpacaAdapter for each test,
    and patches settings to provide a default refresh token.
    """
    monkeypatch.setattr(settings, 'ALPACA_REFRESH_TOKEN', "test_refresh_token")
    adapter = AlpacaAdapter()
    return adapter

@pytest.fixture
def sample_order():
    return Order(
        order_id=1,
        client_order_id="test-order-123",
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
    Tests that check_connection returns True when authentication and account checks are successful.
    """
    respx.post(AlpacaAdapter.AUTH_URL).mock(return_value=Response(200, json={"access_token": "fake_token"}))
    respx.get(f"{settings.ALPACA_API_URL}/v2/account").mock(return_value=Response(200, json={"id": "test_account"}))
    assert await alpaca_adapter.check_connection() is True


@pytest.mark.asyncio
@respx.mock
async def test_check_connection_failure(alpaca_adapter):
    """
    Tests that check_connection returns False when authentication fails.
    """
    respx.post(AlpacaAdapter.AUTH_URL).mock(return_value=Response(500, text="Internal Server Error"))
    assert await alpaca_adapter.check_connection() is False


@pytest.mark.asyncio
@respx.mock
async def test_place_order_success(alpaca_adapter, sample_order):
    """
    Tests a successful order placement workflow.
    """
    auth_request = respx.post(AlpacaAdapter.AUTH_URL).mock(
        return_value=Response(200, json={"access_token": "fake_token", "expires_in": 3600})
    )
    order_request = respx.post(f"{settings.ALPACA_BROKER_URL}/v1/orders").mock(
        return_value=Response(200, json={"id": "broker-order-id-123", "status": "accepted"})
    )

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    assert auth_request.called
    assert order_request.called
    update_callback.assert_awaited_once_with({
        "order_id": sample_order.order_id,
        "status": OrderStatus.PLACED,
        "broker_order_id": "broker-order-id-123",
    })


@pytest.mark.asyncio
@respx.mock
async def test_place_order_auth_failure(alpaca_adapter, sample_order):
    """
    Tests the workflow where the initial authentication with Alpaca fails.
    """
    respx.post(AlpacaAdapter.AUTH_URL).mock(return_value=Response(401, text="Invalid credentials"))

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    update_callback.assert_awaited_once_with({
        "order_id": sample_order.order_id,
        "status": OrderStatus.FAILED,
        "reason": "Authentication failed: Could not get a valid access token.",
    })


@pytest.mark.asyncio
@respx.mock
async def test_place_order_token_expired_and_refresh_success(alpaca_adapter, sample_order):
    """
    Tests the workflow where the access token expires and is successfully refreshed.
    """
    order_route = respx.post(f"{settings.ALPACA_BROKER_URL}/v1/orders")
    order_route.side_effect = [
        Response(401, text="Token expired"),
        Response(200, json={"id": "broker-order-id-456", "status": "accepted"}),
    ]
    auth_route = respx.post(AlpacaAdapter.AUTH_URL).mock(
        return_value=Response(200, json={"access_token": "new_fake_token"})
    )

    # Pre-fill the adapter with a token that appears valid but will be rejected
    alpaca_adapter._access_token = "seemingly_valid_expired_token"
    alpaca_adapter._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    assert auth_route.call_count == 1
    assert order_route.call_count == 2
    update_callback.assert_awaited_once_with({
        "order_id": sample_order.order_id,
        "status": OrderStatus.PLACED,
        "broker_order_id": "broker-order-id-456",
    })


@pytest.mark.asyncio
@respx.mock
async def test_place_order_broker_rejection(alpaca_adapter, sample_order):
    """
    Tests the workflow where the broker rejects the order.
    """
    respx.post(AlpacaAdapter.AUTH_URL).mock(return_value=Response(200, json={"access_token": "fake_token"}))
    respx.post(f"{settings.ALPACA_BROKER_URL}/v1/orders").mock(
        return_value=Response(403, json={"message": "Insufficient buying power"})
    )

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    update_callback.assert_awaited_once_with({
        "order_id": sample_order.order_id,
        "status": OrderStatus.FAILED,
        "reason": 'Broker API request failed with status 403: {"message":"Insufficient buying power"}',
    })
