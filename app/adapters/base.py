from abc import ABC, abstractmethod
from app.models import Order
from typing import Callable, Awaitable

# Define a type hint for the asynchronous callback function that the
# execution service will provide to the adapter.
StatusUpdateCallable = Callable[[dict], Awaitable[None]]

class BrokerAdapter(ABC):
    """
    Abstract base class for all broker adapters.
    Defines the standard interface for placing, canceling,
    and querying orders.
    """

    @abstractmethod
    async def place_order(self, order: Order, update_callback: StatusUpdateCallable):
        """
        Places an order and provides asynchronous status updates via a callback.

        The implementation should invoke the update_callback with a dictionary
        containing state changes as they happen at the broker. This simulates
        an event-driven connection.

        Args:
            order: The internal order object.
            update_callback: An async function to call with status updates.
        """
        ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> dict:
        """
        Cancels a live order at the broker.

        Args:
            broker_order_id: The ID assigned by the broker.

        Returns:
            A dictionary confirming the cancellation.
        """
        ...

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> dict:
        """
        Retrieves the current status of an order from the broker.

        Args:
            broker_order_id: The ID assigned by the broker.

        Returns:
            A dictionary with the latest order state.
        """
        ...
