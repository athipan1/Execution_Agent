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
def alpaca_adapter():
    return AlpacaAdapter()

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
async def test_place_order_success(alpaca_adapter, sample_order):
    """
    Tests a successful order placement workflow.
    """
    # 1. Mock the authentication request
    auth_request = respx.post(settings.ALPACA_AUTH_URL).mock(
        return_value=Response(200, json={"access_token": "fake_token", "expires_in": 3600})
    )
    # 2. Mock the order placement request
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
    respx.post(settings.ALPACA_AUTH_URL).mock(return_value=Response(401, text="Invalid credentials"))

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    update_callback.assert_awaited_once_with({
        "order_id": sample_order.order_id,
        "status": OrderStatus.FAILED,
        "reason": "Authentication failed: Could not get access token.",
    })

@pytest.mark.asyncio
@respx.mock
async def test_place_order_token_expired_and_refresh_success(alpaca_adapter, sample_order):
    """
    Tests the workflow where the access token expires and is successfully refreshed.
    """
    # 1. First order request fails with 401
    order_route = respx.post(f"{settings.ALPACA_BROKER_URL}/v1/orders")
    order_route.side_effect = [
        Response(401, text="Token expired"),
        Response(200, json={"id": "broker-order-id-456", "status": "accepted"}),
    ]
    # 2. Auth request is called to refresh the token
    auth_route = respx.post(settings.ALPACA_AUTH_URL).mock(
        return_value=Response(200, json={"access_token": "new_fake_token"})
    )

    # Pre-fill the adapter with a token that appears valid but will be rejected
    alpaca_adapter._access_token = "seemingly_valid_expired_token"
    alpaca_adapter._token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

    update_callback = AsyncMock()
    await alpaca_adapter.place_order(sample_order, update_callback)

    # The auth route should only be called ONCE, after the 401 failure.
    assert auth_route.call_count == 1
    # The order route should be called twice (initial attempt + retry).
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
    respx.post(settings.ALPACA_AUTH_URL).mock(return_value=Response(200, json={"access_token": "fake_token"}))
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
