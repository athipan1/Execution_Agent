from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from app.models import Order, CreateOrderRequest
import threading

class DatabaseClient(ABC):
    """
    Abstract interface for interacting with the Database Agent.
    """
    @abstractmethod
    def create_order(self, order_data: CreateOrderRequest) -> Order: ...

    @abstractmethod
    def get_order_by_client_id(self, client_order_id: str) -> Optional[Order]: ...

    @abstractmethod
    def get_order_by_order_id(self, order_id: int) -> Optional[Order]: ...

    @abstractmethod
    def update_order(self, order_id: int, updates: Dict[str, Any]) -> Order: ...

class InMemoryDatabaseClient(DatabaseClient):
    """
    An in-memory implementation of the DatabaseClient for development and testing.
    This class is thread-safe to handle concurrent requests.
    """
    def __init__(self):
        self._orders_by_client_id: Dict[str, Order] = {}
        self._orders_by_order_id: Dict[int, Order] = {}
        self._id_seq = 1
        self._lock = threading.Lock()

    def create_order(self, order_data: CreateOrderRequest) -> Order:
        with self._lock:
            if order_data.client_order_id in self._orders_by_client_id:
                raise ValueError("Duplicate client_order_id")

            order_id = self._id_seq
            self._id_seq += 1

            new_order = Order(
                order_id=order_id,
                **order_data.model_dump() # Use model_dump() instead of dict()
            )

            self._orders_by_client_id[new_order.client_order_id] = new_order
            self._orders_by_order_id[order_id] = new_order
            return new_order.model_copy() # Use model_copy()

    def get_order_by_client_id(self, client_order_id: str) -> Optional[Order]:
        with self._lock:
            order = self._orders_by_client_id.get(client_order_id)
            return order.model_copy() if order else None

    def get_order_by_order_id(self, order_id: int) -> Optional[Order]:
        with self._lock:
            order = self._orders_by_order_id.get(order_id)
            return order.model_copy() if order else None

    def update_order(self, order_id: int, updates: Dict[str, Any]) -> Order:
        with self._lock:
            if order_id not in self._orders_by_order_id:
                raise KeyError(f"Order with ID {order_id} not found.")

            stored_order = self._orders_by_order_id[order_id]
            updated_order = stored_order.model_copy(update=updates) # Use model_copy()

            self._orders_by_order_id[order_id] = updated_order
            self._orders_by_client_id[updated_order.client_order_id] = updated_order

            return updated_order.model_copy()

_db_client_instance = None

def get_db_client() -> DatabaseClient:
    global _db_client_instance
    if _db_client_instance is None:
        _db_client_instance = InMemoryDatabaseClient()
    return _db_client_instance
