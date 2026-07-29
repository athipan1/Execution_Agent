import asyncio

import uuid

from typing import Dict, Any, List

from datetime import datetime, timezone

from app.adapters.base import BrokerAdapter, StatusUpdateCallable

from app.models import Order, OrderStatus, OrderSide, TradeOrder

from app.services.protective_order_service import (

    ProtectiveOrderError,

    is_profit_lifecycle_exit,

    validate_profit_lifecycle_exit,

    validate_protection_plan,

)

class SimulatorAdapter(BrokerAdapter):

    """

    A deterministic, in-memory broker simulator for paper trading and testing.

    Safety rule:

    - New simulated entry orders must include a valid protective exit plan.

    - This keeps SIMULATOR behavior aligned with Alpaca Paper/Live behavior.

    - If Manager/Risk/Execution sends an unprotected order, the simulator rejects it.

    """

    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):

        """

        Simulates placing an order with deterministic behavior based on the symbol.

        Important:

        This simulator now requires protection for every new entry order.

        If the order does not include a valid protective plan, it is rejected.

        """

        reduce_only_exit = is_profit_lifecycle_exit(order)

        try:

            protection = (

                validate_profit_lifecycle_exit(order)

                if reduce_only_exit

                else validate_protection_plan(order, required=True)

            )

        except ProtectiveOrderError as exc:

            await update_callback(

                {

                    "order_id": order.order_id,

                    "status": OrderStatus.FAILED,

                    "reason": f"Missing or invalid simulated order safety contract: {exc}",

                    "orders_changed": False,

                }

            )

            return

        broker_order_id = (

            f"sim-profit-exit-{uuid.uuid4()}"

            if reduce_only_exit

            else f"sim-bracket-{uuid.uuid4()}"

        )

        update = {

            "order_id": order.order_id,

            "status": OrderStatus.PLACED,

            "broker_order_id": broker_order_id,

            "order_class": "reduce_only_exit" if reduce_only_exit else "bracket",

            "orders_changed": True,

            "protection_required": not reduce_only_exit,

            "reduce_only_intent": reduce_only_exit,

        }

        if reduce_only_exit:

            update["reason"] = (

                "Simulated reduce-only profit lifecycle exit accepted"

            )

            update["protective_exit"] = protection

        elif protection:

            update["reason"] = (

                f"Simulated protected bracket order placed with "

                f"{protection.get('side')} protective exit at "

                f"{protection.get('trigger_price')}"

            )

            update["protective_exit"] = protection

        await update_callback(update)

        await asyncio.sleep(0.1)

        symbol = order.symbol.upper()

        if "FAIL" in symbol:

            await self._simulate_failure(order, update_callback)

        elif "PARTIAL" in symbol:

            await self._simulate_partial_fill(order, update_callback)

        else:

            await self._simulate_full_execution(order, update_callback)

    async def _simulate_failure(self, order: Order, update_callback: StatusUpdateCallable):

        await update_callback(

            {

                "order_id": order.order_id,

                "status": OrderStatus.FAILED,

                "reason": "Simulated broker rejection for symbol.",

                "orders_changed": False,

            }

        )

    async def _simulate_partial_fill(self, order: Order, update_callback: StatusUpdateCallable):

        partial_quantity = order.quantity // 2

        exec_price = self._calculate_execution_price(order)

        await update_callback(

            {

                "order_id": order.order_id,

                "status": OrderStatus.PARTIALLY_FILLED,

                "executed_quantity": partial_quantity,

                "avg_execution_price": exec_price,

            }

        )

        await asyncio.sleep(0.2)

        await self._simulate_full_execution(

            order,

            update_callback,

            from_partial=True,

            initial_price=exec_price,

        )

    async def _simulate_full_execution(

        self,

        order: Order,

        update_callback: StatusUpdateCallable,

        from_partial: bool = False,

        initial_price: float = 0.0,

    ):

        if from_partial:

            second_exec_price = self._calculate_execution_price(order, slippage=0.002)

            avg_price = (initial_price + second_exec_price) / 2

        else:

            avg_price = self._calculate_execution_price(order)

        await update_callback(

            {

                "order_id": order.order_id,

                "status": OrderStatus.EXECUTED,

                "executed_quantity": order.quantity,

                "avg_execution_price": round(avg_price, 2),

                "executed_at": datetime.now(timezone.utc),

            }

        )

    def _calculate_execution_price(self, order: Order, slippage: float = 0.001) -> float:

        reference_price = order.price if order.price else 100.00

        if order.side == OrderSide.BUY:

            return reference_price * (1 + slippage)

        return reference_price * (1 - slippage)

    async def cancel_order(self, broker_order_id: str) -> dict:

        return {

            "status": OrderStatus.CANCELLED,

            "broker_order_id": broker_order_id,

            "orders_changed": True,

        }

    async def submit_exit_bracket_order(

        self,

        *,

        symbol: str,

        qty: Any,

        side: str,

        stop_price: Any,

        take_profit_price: Any,

        client_order_id: str | None = None,

    ) -> dict:

        """

        Simulate submitting an OCO / bracket-style exit order for an existing position.

        Used by the stop-only upgrade flow:

        existing position + old stop-only order -> replacement TP/SL order.

        """

        broker_order_id = f"sim-oco-{uuid.uuid4()}"

        return {

            "status": OrderStatus.PLACED,

            "broker_order_id": broker_order_id,

            "symbol": symbol.upper(),

            "qty": str(qty),

            "side": side,

            "type": "limit",

            "order_class": "oco",

            "stop_loss": {"stop_price": stop_price},

            "take_profit": {"limit_price": take_profit_price},

            "client_order_id": client_order_id,

            "orders_changed": True,

        }

    async def submit_protective_stop_order(

        self,

        *,

        symbol: str,

        qty: Any,

        side: str,

        stop_price: Any,

        client_order_id: str | None = None,

    ) -> dict:

        """

        Simulate submitting a protective stop order.

        This remains available for rollback protection only.

        New entry orders should not rely on stop-only protection anymore.

        """

        broker_order_id = f"sim-stop-{uuid.uuid4()}"

        return {

            "status": OrderStatus.PLACED,

            "broker_order_id": broker_order_id,

            "symbol": symbol.upper(),

            "qty": str(qty),

            "side": side,

            "type": "stop",

            "order_class": "stop_only",

            "stop_price": stop_price,

            "client_order_id": client_order_id,

            "orders_changed": True,

        }

    async def get_order_status(self, broker_order_id: str) -> dict:

        return {

            "status": OrderStatus.EXECUTED,

            "broker_order_id": broker_order_id,

            "executed_quantity": 100,

            "avg_execution_price": 100.0,

            "executed_at": datetime.now(timezone.utc),

        }

    async def execute(self, trade_order: TradeOrder) -> Dict[str, Any]:

        """

        Direct simulator execution is intentionally blocked.

        Reason:

        TradeOrder does not carry the full protective TP/SL plan.

        To keep simulator behavior aligned with Alpaca, direct execution must not bypass protection.

        Use place_order() with a full Order object that includes protective exit metadata.

        """

        return {

            "status": OrderStatus.FAILED,

            "reason": (

                "Direct SimulatorAdapter.execute() is disabled because TradeOrder does not include "

                "a full TP/SL protective plan. Use place_order() with a protected Order instead."

            ),

            "symbol": trade_order.symbol,

            "side": trade_order.side.value if hasattr(trade_order.side, "value") else trade_order.side,

            "quantity": trade_order.quantity,

            "orders_changed": False,

        }

    async def get_account(self) -> Dict[str, Any]:

        return {

            "broker": "SIMULATOR",

            "cash": "100000.00",

            "buying_power": "100000.00",

            "equity": "100000.00",

            "portfolio_value": "100000.00",

            "paper": True,

            "status": "ACTIVE",

            "trading_blocked": False,

            "transfers_blocked": False,

            "account_blocked": False,

        }

    async def get_positions(self) -> List[Dict[str, Any]]:

        return []

    async def get_open_orders(self) -> List[Dict[str, Any]]:

        return []

    async def check_connection(self) -> bool:

        return True
