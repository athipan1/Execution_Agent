from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Union, List
import httpx
import asyncio
from datetime import datetime, timezone
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from app.models import Order, CreateOrderRequest, ExecutionJob, ExecutionJobStatus
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

    @abstractmethod
    async def create_execution_job(self, order: Order) -> ExecutionJob: ...

    @abstractmethod
    async def get_execution_job(self, job_id: Union[int, str]) -> Optional[ExecutionJob]: ...

    @abstractmethod
    async def get_execution_job_by_order_id(self, order_id: int) -> Optional[ExecutionJob]: ...

    @abstractmethod
    async def claim_next_execution_job(self) -> Optional[ExecutionJob]: ...

    @abstractmethod
    async def update_execution_job(self, job_id: Union[int, str], updates: Dict[str, Any]) -> ExecutionJob: ...

class HttpDatabaseClient(DatabaseClient):
    """
    HTTP implementation of the DatabaseClient that calls the external Database Agent.
    """
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.timeout = 10.0

    def _headers(self) -> Dict[str, str]:
        api_key = settings.DATABASE_AGENT_API_KEY or settings.API_KEY
        return {"X-API-KEY": api_key}

    def _unwrap_standard_response(self, payload: Dict[str, Any]) -> Any:
        if isinstance(payload, dict) and "status" in payload and "data" in payload:
            if payload.get("status") == "error":
                error = payload.get("error") or {}
                raise HTTPException(
                    status_code=502,
                    detail=error.get("message") or "Database Agent returned an error."
                )
            return payload.get("data") or {}
        return payload

    async def create_order(self, order_data: CreateOrderRequest) -> Order:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/accounts/{order_data.account_id}/orders",
                    json=jsonable_encoder(order_data),
                    headers=self._headers()
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise HTTPException(status_code=404, detail=f"Database Agent: Account {order_data.account_id} not found.")
                elif e.response.status_code == 422:
                    raise HTTPException(status_code=422, detail=f"Database Agent: Validation error: {e.response.text}")
                raise
            return Order.model_validate(self._unwrap_standard_response(response.json()))

    async def get_order_by_trade_id(self, trade_id: Union[int, str]) -> Optional[Order]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/orders/trade/{trade_id}",
                headers=self._headers()
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return Order.model_validate(self._unwrap_standard_response(response.json()))

    async def get_order_by_order_id(self, order_id: int) -> Optional[Order]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/orders/{order_id}",
                headers=self._headers()
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return Order.model_validate(self._unwrap_standard_response(response.json()))

    async def update_order(self, order_id: int, updates: Dict[str, Any]) -> Order:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/orders/{order_id}",
                json=jsonable_encoder(updates),
                headers=self._headers()
            )
            response.raise_for_status()
            return Order.model_validate(self._unwrap_standard_response(response.json()))

    async def create_execution_job(self, order: Order) -> ExecutionJob:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/execution-jobs",
                json=jsonable_encoder({"order_id": order.order_id, "trade_id": order.trade_id}),
                headers=self._headers(),
            )
            response.raise_for_status()
            return ExecutionJob.model_validate(self._unwrap_standard_response(response.json()))

    async def get_execution_job(self, job_id: Union[int, str]) -> Optional[ExecutionJob]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/execution-jobs/{job_id}", headers=self._headers())
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return ExecutionJob.model_validate(self._unwrap_standard_response(response.json()))

    async def get_execution_job_by_order_id(self, order_id: int) -> Optional[ExecutionJob]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/orders/{order_id}/execution-job", headers=self._headers())
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return ExecutionJob.model_validate(self._unwrap_standard_response(response.json()))

    async def claim_next_execution_job(self) -> Optional[ExecutionJob]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/execution-jobs/claim-next", headers=self._headers())
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = self._unwrap_standard_response(response.json())
            return ExecutionJob.model_validate(data) if data else None

    async def update_execution_job(self, job_id: Union[int, str], updates: Dict[str, Any]) -> ExecutionJob:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}/execution-jobs/{job_id}",
                json=jsonable_encoder(updates),
                headers=self._headers(),
            )
            response.raise_for_status()
            return ExecutionJob.model_validate(self._unwrap_standard_response(response.json()))

class InMemoryDatabaseClient(DatabaseClient):
    """
    An in-memory implementation of the DatabaseClient for development and testing.
    This class is thread-safe to handle concurrent requests.
    """
    def __init__(self):
        self._orders_by_trade_id: Dict[Union[int, str], Order] = {}
        self._orders_by_order_id: Dict[int, Order] = {}
        self._jobs_by_id: Dict[Union[int, str], ExecutionJob] = {}
        self._jobs_by_order_id: Dict[int, ExecutionJob] = {}
        self._id_seq = 1
        self._job_id_seq = 1
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

    async def create_execution_job(self, order: Order) -> ExecutionJob:
        async with self._lock:
            existing = self._jobs_by_order_id.get(order.order_id)
            if existing:
                return existing.model_copy()
            job = ExecutionJob(
                job_id=self._job_id_seq,
                order_id=order.order_id,
                trade_id=order.trade_id,
                status=ExecutionJobStatus.QUEUED,
            )
            self._job_id_seq += 1
            self._jobs_by_id[job.job_id] = job
            self._jobs_by_order_id[order.order_id] = job
            return job.model_copy()

    async def get_execution_job(self, job_id: Union[int, str]) -> Optional[ExecutionJob]:
        async with self._lock:
            job = self._jobs_by_id.get(job_id)
            return job.model_copy() if job else None

    async def get_execution_job_by_order_id(self, order_id: int) -> Optional[ExecutionJob]:
        async with self._lock:
            job = self._jobs_by_order_id.get(order_id)
            return job.model_copy() if job else None

    async def claim_next_execution_job(self) -> Optional[ExecutionJob]:
        async with self._lock:
            for job in self._jobs_by_id.values():
                if job.status == ExecutionJobStatus.QUEUED and job.attempts < job.max_attempts:
                    updated = job.model_copy(update={
                        "status": ExecutionJobStatus.RUNNING,
                        "attempts": job.attempts + 1,
                        "updated_at": datetime.now(timezone.utc),
                    })
                    self._jobs_by_id[job.job_id] = updated
                    self._jobs_by_order_id[job.order_id] = updated
                    return updated.model_copy()
            return None

    async def update_execution_job(self, job_id: Union[int, str], updates: Dict[str, Any]) -> ExecutionJob:
        async with self._lock:
            if job_id not in self._jobs_by_id:
                raise KeyError(f"Execution job with ID {job_id} not found.")
            stored_job = self._jobs_by_id[job_id]
            update_payload = dict(updates)
            update_payload["updated_at"] = update_payload.get("updated_at") or datetime.now(timezone.utc)
            updated = stored_job.model_copy(update=update_payload)
            self._jobs_by_id[job_id] = updated
            self._jobs_by_order_id[updated.order_id] = updated
            return updated.model_copy()

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
