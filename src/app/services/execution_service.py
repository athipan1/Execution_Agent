from app.models import CreateOrderRequest, Order, OrderStatus
from app.db_client import DatabaseClient
from app.adapters.base import BrokerAdapter
from app.logging import get_logger
from typing import Dict, Any, Optional

logger = get_logger(__name__)


class ExecutionService:
    """
    Orchestrates the order lifecycle, coordinating between the database
    and the broker.
    """
    def __init__(self, db_client: DatabaseClient, broker_adapter: BrokerAdapter):
        self.db_client = db_client
        self.broker_adapter = broker_adapter

    async def create_order(self, order_request: CreateOrderRequest) -> Order:
        """
        Creates a new order, ensuring idempotency.
        """
        existing_order = await self.db_client.get_order_by_trade_id(order_request.trade_id)
        if existing_order:
            logger.info(
                "Idempotent request received for existing order.",
                extra={"trade_id": order_request.trade_id, "order_id": existing_order.order_id}
            )
            return existing_order

        new_order = await self.db_client.create_order(order_request)
        logger.info(
            "New order created in pending state.",
            extra={"trade_id": new_order.trade_id, "order_id": new_order.order_id}
        )
        return new_order

    async def _handle_broker_updates(self, updates: Dict[str, Any]):
        """
        Callback function passed to the broker adapter.
        It receives status updates and persists them to the database.
        """
        order_id = updates.get("order_id")
        if not order_id:
            logger.error("Received broker update without an order_id.", extra={"update_data": updates})
            return

        logger.info(
            "Received broker update for order.",
            extra={"order_id": order_id, "status": updates.get("status")}
        )
        await self.db_client.update_order(order_id, updates)

    async def refresh_order_status(self, order_id: int) -> Optional[Order]:
        """
        Fetches the latest status from the broker and updates the database.
        """
        order = await self.db_client.get_order_by_order_id(order_id)
        if not order:
            return None

        if not order.broker_order_id:
            return order

        if order.status in [OrderStatus.EXECUTED, OrderStatus.FAILED, OrderStatus.CANCELLED]:
            return order

        updates = await self.broker_adapter.get_order_status(order.broker_order_id)
        if updates.get("status") != "error":
            updates["order_id"] = order_id
            await self._handle_broker_updates(updates)
            return await self.db_client.get_order_by_order_id(order_id)

        return order

    async def start_order_execution(self, order: Order) -> Order:
        """
        Places a persisted pending order with the broker and returns the latest stored order.
        This is awaited by the caller rather than scheduled as an in-process background task.
        """
        logger.info(
            "Starting execution for order.",
            extra={"order_id": order.order_id, "symbol": order.symbol}
        )
        try:
            await self.broker_adapter.place_order(order, self._handle_broker_updates)
            return await self.db_client.get_order_by_order_id(order.order_id) or order
        except Exception as e:
            logger.error(
                "Order execution failed.",
                extra={"order_id": order.order_id, "error": str(e)},
                exc_info=True
            )
            return await self.db_client.update_order(order.order_id, {"status": OrderStatus.FAILED, "error_message": str(e)})
