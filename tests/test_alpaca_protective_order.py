from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response

from app.adapters.alpaca import AlpacaAdapter
from app.config import settings
from app.models import Order, OrderSide, OrderType, TimeInForce, OrderStatus

settings.ALPACA_API_KEY_ID = "test_api_key_id"
settings.ALPACA_SECRET_KEY = "test_secret_key"


def protected_order(**overrides):
    data = {
        "order_id": 1,
        "trade_id": "trade-protected",
        "account_id": 1,
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 10,
        "time_in_force": TimeInForce.GTC,
        "guard_plan": {"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90},
    }
    data.update(overrides)
    return Order(**data)


@pytest.mark.asyncio
@respx.mock
async def test_alpaca_entry_payload_includes_oto_stop_loss():
    adapter = AlpacaAdapter()
    order_request = respx.post(f"{settings.ALPACA_API_URL}/v2/orders").mock(
        return_value=Response(200, json={"id": "broker-order-id-123", "status": "accepted"})
    )

    update_callback = AsyncMock()
    await adapter.place_order(protected_order(), update_callback)

    assert order_request.called
    payload = order_request.calls.last.request.content.decode("utf-8")
    assert '"order_class":"oto"' in payload
    assert '"stop_loss":{"stop_price":"90.0"}' in payload
    update_callback.assert_awaited_once_with({
        "order_id": 1,
        "status": OrderStatus.PLACED,
        "broker_order_id": "broker-order-id-123",
        "executed_quantity": 0,
    })


@pytest.mark.asyncio
@respx.mock
async def test_live_alpaca_refuses_unprotected_order_before_broker_call():
    adapter = AlpacaAdapter()
    order_route = respx.post(f"{settings.ALPACA_API_URL}/v2/orders").mock(
        return_value=Response(200, json={"id": "should-not-be-called", "status": "accepted"})
    )

    update_callback = AsyncMock()
    with patch("app.adapters.alpaca.settings.TRADING_MODE", "LIVE"):
        await adapter.place_order(protected_order(guard_plan=None, protective_exit=None), update_callback)

    assert not order_route.called
    update = update_callback.await_args.args[0]
    assert update["status"] == OrderStatus.FAILED
    assert "guard_plan" in update["reason"]


@pytest.mark.asyncio
@respx.mock
async def test_invalid_guard_plan_fails_without_broker_call():
    adapter = AlpacaAdapter()
    order_route = respx.post(f"{settings.ALPACA_API_URL}/v2/orders").mock(
        return_value=Response(200, json={"id": "should-not-be-called", "status": "accepted"})
    )

    bad_order = protected_order(guard_plan={"symbol": "AAPL", "side": "buy", "quantity": 10, "trigger_price": 90})
    update_callback = AsyncMock()
    await adapter.place_order(bad_order, update_callback)

    assert not order_route.called
    update = update_callback.await_args.args[0]
    assert update["status"] == OrderStatus.FAILED
    assert "protective side" in update["reason"]
