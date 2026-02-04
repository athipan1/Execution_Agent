import pytest
from unittest.mock import AsyncMock
from app.adapters.simulator import SimulatorAdapter
from app.models import Order, OrderSide, OrderType, TimeInForce

# Mark all tests in this file as async tests
pytestmark = pytest.mark.asyncio

@pytest.fixture
def simulator():
    return SimulatorAdapter()

@pytest.fixture
def base_order():
    return Order(
        order_id=1,
        trade_id="test-client-id",
        account_id=123,
        symbol="TEST.BK",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=100,
        time_in_force=TimeInForce.GTC
    )

async def test_successful_execution(simulator: SimulatorAdapter, base_order: Order):
    """
    Tests the standard execution path.
    """
    mock_callback = AsyncMock()
    await simulator.place_order(base_order, mock_callback)

    # Check that the callback was called twice: once for 'placed', once for 'executed'
    assert mock_callback.call_count == 2

    # Check the 'placed' call
    placed_call_args = mock_callback.call_args_list[0].args[0]
    assert placed_call_args["status"] == "placed"
    assert "broker_order_id" in placed_call_args

    # Check the 'executed' call
    executed_call_args = mock_callback.call_args_list[1].args[0]
    assert executed_call_args["status"] == "executed"
    assert executed_call_args["executed_quantity"] == 100
    assert executed_call_args["avg_execution_price"] > 0

async def test_failed_execution(simulator: SimulatorAdapter, base_order: Order):
    """
    Tests the failure path using the special symbol.
    """
    base_order.symbol = "FAIL.BK"
    mock_callback = AsyncMock()

    await simulator.place_order(base_order, mock_callback)

    assert mock_callback.call_count == 2 # placed -> failed

    failed_call_args = mock_callback.call_args_list[1].args[0]
    assert failed_call_args["status"] == "failed"
    assert "rejection" in failed_call_args["reason"]

async def test_partial_fill_execution(simulator: SimulatorAdapter, base_order: Order):
    """
    Tests the partial fill -> full execution path.
    """
    base_order.symbol = "PARTIAL.BK"
    mock_callback = AsyncMock()

    await simulator.place_order(base_order, mock_callback)

    # Expect 3 calls: placed -> partially_filled -> executed
    assert mock_callback.call_count == 3

    # Check partial fill state
    partial_fill_args = mock_callback.call_args_list[1].args[0]
    assert partial_fill_args["status"] == "partially_filled"
    assert partial_fill_args["executed_quantity"] == base_order.quantity // 2

    # Check final executed state
    executed_args = mock_callback.call_args_list[2].args[0]
    assert executed_args["status"] == "executed"
    assert executed_args["executed_quantity"] == base_order.quantity
