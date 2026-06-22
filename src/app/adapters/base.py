from abc import ABC, abstractmethod
from app.models import Order, TradeOrder
from typing import Callable, Awaitable, Any, Dict, List

# Define a type hint for the asynchronous callback function that the
# execution service will provide to the adapter.
StatusUpdateCallable = Callable[[dict], Awaitable[None]]

class BrokerAdapter(ABC):
    """
    Abstract base class for all broker adapters.
    Defines the standard interface for placing, canceling,
    querying orders, and reading account/portfolio state.
    """

    @abstractmethod
    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):
        """
        Places an order and provides asynchronous status updates via a callback.
        """
        ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> dict:
        """Cancels a live order at the broker."""
        ...

    async def cancel_open_order(self, broker_order: Dict[str, Any]) -> dict:
        """Cancels an open broker order object returned by get_open_orders()."""
        broker_order_id = str(broker_order.get("id") or broker_order.get("broker_order_id") or "")
        if not broker_order_id:
            return {"status": "error", "message": "broker order id missing", "order": broker_order}
        result = await self.cancel_order(broker_order_id)
        return {**result, "broker_order_id": broker_order_id, "symbol": broker_order.get("symbol"), "side": broker_order.get("side"), "qty": broker_order.get("qty")}

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> dict:
        """Retrieves the current status of an order from the broker."""
        ...

    @abstractmethod
    async def execute(self, trade_order: TradeOrder) -> Dict[str, Any]:
        """Executes a trade directly and returns the result."""
        ...

    @abstractmethod
    async def get_account(self) -> Dict[str, Any]:
        """Returns account buying power/cash/equity data from the broker."""
        ...

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Returns current open positions from the broker."""
        ...

    @abstractmethod
    async def get_open_orders(self) -> List[Dict[str, Any]]:
        """Returns currently open broker orders."""
        ...

    @abstractmethod
    async def check_connection(self) -> bool:
        """Verifies the connection to the broker."""
        ...
