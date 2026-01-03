import pytest
from unittest.mock import AsyncMock
from app.adapters.real import RealBrokerAdapter
from app.models import Order, OrderStatus, OrderSide, OrderType, TimeInForce

@pytest.mark.asyncio
async def test_place_order_simulated_success():
    """
    Tests that the placeholder RealBrokerAdapter correctly simulates
    a successful order placement and execution.
    """
    adapter = RealBrokerAdapter()
    order = Order(
        order_id=1,
        client_order_id="test-client-order",
        account_id=123,
        symbol="AAPL",
        quantity=100,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=150.0,
        time_in_force=TimeInForce.GTC,
        status=OrderStatus.PENDING,
    )

    # Mock the async callback function
    update_callback = AsyncMock()

    # Call the method under test
    await adapter.place_order(order, update_callback)

    # Assertions
    assert update_callback.call_count == 2

    # First call: PLACED
    placed_call = update_callback.call_args_list[0]
    placed_update = placed_call.args[0]
    assert placed_update["status"] == OrderStatus.PLACED
    assert "broker_order_id" in placed_update
    assert placed_update["order_id"] == order.order_id

    # Second call: EXECUTED
    executed_call = update_callback.call_args_list[1]
    executed_update = executed_call.args[0]
    assert executed_update["status"] == OrderStatus.EXECUTED
    assert executed_update["executed_quantity"] == order.quantity
    assert executed_update["avg_execution_price"] is not None
    assert executed_update["order_id"] == order.order_id
