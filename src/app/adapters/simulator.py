import asyncio
import uuid
from typing import Dict, Any
from datetime import datetime, timezone
from app.adapters.base import BrokerAdapter, StatusUpdateCallable
from app.models import Order, OrderStatus, OrderSide, TradeOrder

class SimulatorAdapter(BrokerAdapter):
    """
    A deterministic, in-memory broker simulator for paper trading and testing.

    It uses the order's symbol to decide the execution outcome, allowing for
    predictable testing of the entire order lifecycle.
    """

    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):
        """
        Simulates placing an order with deterministic behavior based on the symbol.
        """
        broker_order_id = f"sim-{uuid.uuid4()}"

        # 1. Immediately acknowledge the order as 'placed'
        await update_callback({
            "order_id": order.order_id,
            "status": OrderStatus.PLACED,
            "broker_order_id": broker_order_id
        })

        # 2. Simulate execution based on symbol
        await asyncio.sleep(0.1) # Simulate network latency

        symbol = order.symbol.upper()
        if "FAIL" in symbol:
            await self._simulate_failure(order, update_callback)
        elif "PARTIAL" in symbol:
            await self._simulate_partial_fill(order, update_callback)
        else:
            await self._simulate_full_execution(order, update_callback)

    async def _simulate_failure(self, order: Order, update_callback: StatusUpdateCallable):
        await update_callback({
            "order_id": order.order_id,
            "status": OrderStatus.FAILED,
            "reason": "Simulated broker rejection for symbol."
        })

    async def _simulate_partial_fill(self, order: Order, update_callback: StatusUpdateCallable):
        # First, a partial fill
        partial_quantity = order.quantity // 2
        exec_price = self._calculate_execution_price(order)

        await update_callback({
            "order_id": order.order_id,
            "status": OrderStatus.PARTIALLY_FILLED,
            "executed_quantity": partial_quantity,
            "avg_execution_price": exec_price
        })

        # Then, after a delay, the final execution
        await asyncio.sleep(0.2)
        await self._simulate_full_execution(order, update_callback, from_partial=True, initial_price=exec_price)

    async def _simulate_full_execution(self, order: Order, update_callback: StatusUpdateCallable, from_partial=False, initial_price=0.0):
        if from_partial:
            # If coming from a partial fill, calculate the price for the remaining part
            # and then average it with the initial price. This is a simplification.
            second_exec_price = self._calculate_execution_price(order, slippage=0.002)
            avg_price = (initial_price + second_exec_price) / 2
        else:
            avg_price = self._calculate_execution_price(order)

        await update_callback({
            "order_id": order.order_id,
            "status": OrderStatus.EXECUTED,
            "executed_quantity": order.quantity,
            "avg_execution_price": round(avg_price, 2),
            "executed_at": datetime.now(timezone.utc)
        })

    def _calculate_execution_price(self, order: Order, slippage: float = 0.001) -> float:
        """Calculates a deterministic execution price with slippage."""
        # For market orders, we need a reference price. In a real system,
        # this would come from a market data feed. Here, we'll invent one.
        reference_price = order.price if order.price else 100.00

        if order.side == OrderSide.BUY:
            return reference_price * (1 + slippage)
        else: # SELL
            return reference_price * (1 - slippage)

    async def cancel_order(self, broker_order_id: str) -> dict:
        # In this simple simulator, we assume any live order can be cancelled.
        # A more complex simulator could track inflight orders.
        return {"status": OrderStatus.CANCELLED}

    async def get_order_status(self, broker_order_id: str) -> dict:
        # This would require the simulator to maintain state, which is out of scope
        # for this simple implementation. The primary update mechanism is the callback.
        # We return a placeholder response.
        return {"status": OrderStatus.PLACED, "executed_quantity": 0}

    async def execute(self, trade_order: TradeOrder) -> Dict[str, Any]:
        """
        Directly executes a trade in the simulator.
        """
        broker_order_id = f"sim-exec-{uuid.uuid4()}"
        symbol = trade_order.symbol.upper()

        if "FAIL" in symbol:
            return {
                "status": OrderStatus.FAILED,
                "reason": "Simulated broker rejection for symbol.",
                "broker_order_id": broker_order_id
            }

        # For the direct execute, we return success immediately
        # We'll calculate a price using the same logic
        # TradeOrder doesn't have price, so we'll use a default
        reference_price = 100.0
        slippage = 0.001
        if trade_order.side == OrderSide.BUY:
            avg_price = reference_price * (1 + slippage)
        else:
            avg_price = reference_price * (1 - slippage)

        return {
            "status": OrderStatus.EXECUTED,
            "broker_order_id": broker_order_id,
            "symbol": trade_order.symbol,
            "side": trade_order.side,
            "quantity": trade_order.quantity,
            "avg_execution_price": round(avg_price, 2),
            "executed_at": datetime.now(timezone.utc)
        }

    async def check_connection(self) -> bool:
        """
        Simulator is always connected.
        """
        return True
