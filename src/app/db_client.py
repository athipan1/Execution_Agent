from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Union
import httpx
import asyncio
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from app.models import Order, CreateOrderRequest
from app.config import settings
from app.logging import get_logger

logger = get_logger(__name__)

class DatabaseClient(ABC):
    """
    Abstract interface for interacting with the Database Agent.
    """
    @abstractmethod
    async def create_order(self, order_data: CreateOrderRequest) -> Order: ...

    @abstractmethod
    async def get_order_by_trade_id(self, trade_id: Union[int, str]) -> Optional[Order]: ...

    @abstractmethod
    async def get_order_by_order_id(self, order_id: int) -> Optional[Order]: ...

    @abstractmethod
    async def update_order(self, order_id: int, updates: Dict[str, Any]) -> Order: ...

class HttpDatabaseClient(DatabaseClient):
    """
    HTTP implementation of the DatabaseClient that calls the external Database Agent.
    """
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.timeout = 10.0

    async def create_order(self, order_data: CreateOrderRequest) -> Order:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/accounts/{order_data.account_id}/orders",
                    json=jsonable_encoder(order_data),
                    headers={"X-API-KEY": settings.API_KEY}
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise HTTPException(status_code=404, detail=f"Database Agent: Account {order_data.account_id} not found.")
                elif e.response.status_code == 422:
                    raise HTTPException(status_code=422, detail=f"Database Agent: Validation error: {e.response.text}")
                raise
            return Order.model_validate(response.json())

    async def get_order_by_trade_id(self, trade_id: Union[int, str]) -> Optional[Order]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/orders/trade/{trade_id}",
                headers={"X-API-KEY": settings.API_KEY}
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return Order.model_validate(response.json())

    async def get_order_by_order_id(self, order_id: int) -> Optional[Order]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/orders/{order_id}",
                headers={"X-API-KEY": settings.API_KEY}
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return Order.model_validate(response.json())

    async def update_order(self, order_id: int, updates: Dict[str, Any]) -> Order:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/orders/{order_id}",
                json=jsonable_encoder(updates),
                headers={"X-API-KEY": settings.API_KEY}
            )
            response.raise_for_status()
            return Order.model_validate(response.json())

class InMemoryDatabaseClient(DatabaseClient):
    """
    An in-memory implementation of the DatabaseClient for development and testing.
    This class is thread-safe to handle concurrent requests.
    """
    def __init__(self):
        self._orders_by_trade_id: Dict[Union[int, str], Order] = {}
        self._orders_by_order_id: Dict[int, Order] = {}
        self._id_seq = 1
        self._lock = asyncio.Lock()

    async def create_order(self, order_data: CreateOrderRequest) -> Order:
        async with self._lock:
            if order_data.trade_id in self._orders_by_trade_id:
                raise ValueError("Duplicate trade_id")

            order_id = self._id_seq
            self._id_seq += 1

            new_order = Order(
                order_id=order_id,
                **order_data.model_dump()
            )

            self._orders_by_trade_id[new_order.trade_id] = new_order
            self._orders_by_order_id[order_id] = new_order
            return new_order.model_copy()

    async def get_order_by_trade_id(self, trade_id: Union[int, str]) -> Optional[Order]:
        async with self._lock:
            order = self._orders_by_trade_id.get(trade_id)
            return order.model_copy() if order else None

    async def get_order_by_order_id(self, order_id: int) -> Optional[Order]:
        async with self._lock:
            order = self._orders_by_order_id.get(order_id)
            return order.model_copy() if order else None

    async def update_order(self, order_id: int, updates: Dict[str, Any]) -> Order:
        async with self._lock:
            if order_id not in self._orders_by_order_id:
                raise KeyError(f"Order with ID {order_id} not found.")

            stored_order = self._orders_by_order_id[order_id]
            updated_order = stored_order.model_copy(update=updates)

            self._orders_by_order_id[order_id] = updated_order
            self._orders_by_trade_id[updated_order.trade_id] = updated_order

            return updated_order.model_copy()

_db_client_instance = None

def get_db_client() -> DatabaseClient:
    global _db_client_instance
    if _db_client_instance is None:
        if settings.DB_AGENT_URL:
            logger.info(f"Using HttpDatabaseClient with URL: {settings.DB_AGENT_URL}")
            _db_client_instance = HttpDatabaseClient(settings.DB_AGENT_URL)
        else:
            logger.warning("DB_AGENT_URL not set. Falling back to InMemoryDatabaseClient (not recommended for production).")
            _db_client_instance = InMemoryDatabaseClient()
    return _db_client_instance
