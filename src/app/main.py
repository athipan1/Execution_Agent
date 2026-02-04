import uuid
from fastapi import FastAPI, Depends, Header, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from typing import Optional, Any

from app.models import (
    CreateOrderRequest, OrderResponse, CreateOrderResponse, Order, OrderStatus,
    StandardAgentResponse, ErrorDetail, ExecutionResult, HealthResponse
)
from app.services.execution_service import ExecutionService
from app.db_client import get_db_client, DatabaseClient
from app.adapters.base import BrokerAdapter
from app.adapters.simulator import SimulatorAdapter
from app.adapters.alpaca import AlpacaAdapter
from app.config import settings
from app.logging import get_logger

app = FastAPI(
    title="Execution Agent",
    description="A production-grade service for executing trading orders.",
    version="1.0.0"
)

logger = get_logger(__name__)

# --- Dependency Injection ---
def get_broker_adapter() -> BrokerAdapter:
    """
    Creates the appropriate broker adapter based on configuration.
    """
    if settings.BROKER_MODE == "ALPACA":
        return AlpacaAdapter()
    elif settings.BROKER_MODE == "SIMULATOR":
        return SimulatorAdapter()
    else:
        logger.warning(
            f"Unknown BROKER_MODE '{settings.BROKER_MODE}'. Defaulting to SIMULATOR.",
            extra={"broker_mode": settings.BROKER_MODE},
        )
        return SimulatorAdapter()

def get_execution_service(
    broker_adapter: BrokerAdapter = Depends(get_broker_adapter)
) -> ExecutionService:
    """
    Creates an ExecutionService with the appropriate broker adapter
    based on the application's configuration.
    """
    db_client = get_db_client()
    return ExecutionService(db_client, broker_adapter)

# --- Middleware & Exception Handlers ---

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.url.path in ["/health", "/health/alpaca", "/docs", "/openapi.json"]:
        return await call_next(request)

    api_key = request.headers.get("X-API-KEY")
    if not api_key or api_key != settings.API_KEY:
        return JSONResponse(
            status_code=401,
            content=StandardAgentResponse(
                status="error",
                error=ErrorDetail(code="HTTP_401", message="Invalid or missing API key").model_dump()
            ).model_dump(mode="json")
        )

    return await call_next(request)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=StandardAgentResponse(
            status="error",
            error=ErrorDetail(code=f"HTTP_{exc.status_code}", message=exc.detail).model_dump()
        ).model_dump(mode="json")
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=StandardAgentResponse(
            status="error",
            error=ErrorDetail(code="INTERNAL_ERROR", message=str(exc)).model_dump()
        ).model_dump(mode="json")
    )

def wrap_success(data: Any, confidence_score: float = 1.0) -> StandardAgentResponse[Any]:
    return StandardAgentResponse(
        status="success",
        data=data,
        confidence_score=confidence_score
    )

# --- API Endpoints ---

@app.post("/execute", response_model=StandardAgentResponse[CreateOrderResponse], status_code=200)
@app.post("/execute_trade", response_model=StandardAgentResponse[CreateOrderResponse], status_code=200, include_in_schema=False)
async def create_order(
    order_request: CreateOrderRequest,
    background_tasks: BackgroundTasks,
    service: ExecutionService = Depends(get_execution_service),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """
    Primary endpoint for creating and executing trade orders.
    /execute_trade is provided as an alias for backward compatibility.
    """
    order_request.trade_id = idempotency_key or order_request.trade_id
    order = await service.create_order(order_request)
    if order.status == OrderStatus.PENDING:
        background_tasks.add_task(service.start_order_execution, order)

    # Explicitly return CreateOrderResponse to match Manager schema
    return wrap_success(CreateOrderResponse.model_validate(order))

@app.get("/execute/{order_id}", response_model=StandardAgentResponse[Order])
async def get_order(order_id: int, db_client: DatabaseClient = Depends(get_db_client)):
    order = await db_client.get_order_by_order_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return wrap_success(order)

@app.post("/execute/{order_id}/cancel", response_model=StandardAgentResponse[Order])
async def cancel_order(order_id: int, service: ExecutionService = Depends(get_execution_service)):
    order = await service.db_client.get_order_by_order_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in [OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED]:
        raise HTTPException(status_code=400, detail=f"Order in state '{order.status}' cannot be cancelled.")

    if order.broker_order_id:
        cancellation_result = await service.broker_adapter.cancel_order(order.broker_order_id)
        if cancellation_result.get("status") == OrderStatus.CANCELLED:
            updated_order = await service.db_client.update_order(order_id, {"status": OrderStatus.CANCELLED})
            return wrap_success(updated_order)

    raise HTTPException(status_code=500, detail="Broker failed to cancel the order.")

def get_alpaca_adapter() -> AlpacaAdapter:
    """Dependency injector for the AlpacaAdapter."""
    return AlpacaAdapter()


# Health check endpoints

@app.get("/health", response_model=StandardAgentResponse[HealthResponse])
async def health_check(adapter: BrokerAdapter = Depends(get_broker_adapter)):
    broker_connected = await adapter.check_connection()
    return wrap_success(HealthResponse(
        status="healthy" if broker_connected else "degraded",
        broker_connected=broker_connected,
        mode=settings.BROKER_MODE
    ))

@app.get("/health/alpaca", response_model=StandardAgentResponse[HealthResponse])
async def health_check_alpaca(adapter: AlpacaAdapter = Depends(get_alpaca_adapter)):
    """Checks the connection to the Alpaca API."""
    connected = await adapter.check_connection()
    if not connected:
        raise HTTPException(
            status_code=503,
            detail="Could not connect to Alpaca.",
        )
    return wrap_success(HealthResponse(
        status="healthy",
        broker_connected=True,
        mode="ALPACA"
    ))
