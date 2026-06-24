import uuid
from fastapi import FastAPI, Depends, Header, Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional, Any, Dict, List, Union

from app.models import (
    CreateOrderRequest, CreateOrderResponse, Order, OrderStatus,
    StandardAgentResponse, ErrorDetail, HealthResponse, ExecutionJob,
    ExecutionJobStatus,
    ReconciliationReport,
)
from app.services.execution_service import ExecutionService, RiskApprovalError
from app.services.broker_preflight import BrokerPreflightError
from app.services.broker_cleanup import BrokerCleanupService
from app.services.broker_state_reconciliation import BrokerStateReconciliationService
from app.services.bucket_order_safety import validate_bucket_order_batch
from app.db_client import get_db_client
from app.adapters.base import BrokerAdapter
from app.adapters.simulator import SimulatorAdapter
from app.adapters.alpaca import AlpacaAdapter
from app.config import settings
from app.logging import get_logger

app = FastAPI(title="Execution Agent", description="A production-grade service for executing trading orders.", version="1.3.0")
logger = get_logger(__name__)


def _trading_mode() -> str:
    return str(settings.TRADING_MODE or "PAPER").upper()


def _broker_mode() -> str:
    return str(settings.BROKER_MODE or "").upper()


def _validate_broker_mode() -> str:
    trading_mode = _trading_mode()
    broker_mode = _broker_mode()
    if trading_mode not in {"PAPER", "LIVE"}:
        raise RuntimeError("TRADING_MODE must be PAPER or LIVE.")
    if trading_mode == "LIVE":
        if not settings.ALLOW_LIVE_TRADING:
            raise RuntimeError("LIVE execution requires ALLOW_LIVE_TRADING=true.")
        if broker_mode != "ALPACA":
            raise RuntimeError("LIVE execution requires BROKER_MODE=ALPACA; simulator fallback is forbidden.")
    if broker_mode not in {"SIMULATOR", "ALPACA"}:
        raise RuntimeError(f"Unsupported BROKER_MODE '{settings.BROKER_MODE}'.")
    return broker_mode


def _ensure_trading_enabled() -> None:
    if not settings.TRADING_ENABLED:
        raise HTTPException(status_code=423, detail="Trading is disabled by TRADING_ENABLED=false.")


def _should_process_batch_now(auto_process: Optional[bool]) -> bool:
    if auto_process is not None:
        return bool(auto_process) and _trading_mode() == "PAPER"
    return _trading_mode() == "PAPER"


def get_broker_adapter() -> BrokerAdapter:
    broker_mode = _validate_broker_mode()
    if broker_mode == "ALPACA":
        return AlpacaAdapter()
    return SimulatorAdapter()


def get_execution_service(broker_adapter: BrokerAdapter = Depends(get_broker_adapter)) -> ExecutionService:
    return ExecutionService(get_db_client(), broker_adapter)


def get_broker_cleanup_service(broker_adapter: BrokerAdapter = Depends(get_broker_adapter)) -> BrokerCleanupService:
    return BrokerCleanupService(broker_adapter)


def get_broker_state_reconciliation_service(broker_adapter: BrokerAdapter = Depends(get_broker_adapter)) -> BrokerStateReconciliationService:
    return BrokerStateReconciliationService(broker_adapter)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.url.path in ["/health", "/health/alpaca", "/docs", "/openapi.json"]:
        return await call_next(request)
    api_key = request.headers.get("X-API-KEY")
    if not api_key or api_key != settings.API_KEY:
        return JSONResponse(status_code=401, content=StandardAgentResponse(status="error", error=ErrorDetail(code="HTTP_401", message="Invalid or missing API key").model_dump()).model_dump(mode="json"))
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content=StandardAgentResponse(status="error", error=ErrorDetail(code=f"HTTP_{exc.status_code}", message=exc.detail).model_dump()).model_dump(mode="json"))


@app.exception_handler(RiskApprovalError)
async def risk_approval_exception_handler(request: Request, exc: RiskApprovalError):
    return JSONResponse(status_code=403, content=StandardAgentResponse(status="error", error=ErrorDetail(code="RISK_APPROVAL_REJECTED", message=str(exc)).model_dump()).model_dump(mode="json"))


@app.exception_handler(BrokerPreflightError)
async def broker_preflight_exception_handler(request: Request, exc: BrokerPreflightError):
    return JSONResponse(status_code=409, content=StandardAgentResponse(status="error", error=ErrorDetail(code="BROKER_PREFLIGHT_REJECTED", message=str(exc)).model_dump()).model_dump(mode="json"))


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(status_code=500, content=StandardAgentResponse(status="error", error=ErrorDetail(code="INTERNAL_ERROR", message=str(exc)).model_dump()).model_dump(mode="json"))


def wrap_success(data: Any, confidence_score: float = 1.0) -> StandardAgentResponse[Any]:
    return StandardAgentResponse(status="success", data=data, confidence_score=confidence_score)


def _open_order_symbols(open_orders: List[Dict[str, Any]]) -> List[str]:
    return [str(order.get("symbol") or "").upper() for order in open_orders if order.get("symbol")]


def _order_response_payload(order: Order) -> Dict[str, Any]:
    return CreateOrderResponse.model_validate(order).model_dump(mode="json")


async def _process_order_now(service: ExecutionService, order: Order, job: ExecutionJob) -> tuple[Order, ExecutionJob]:
    latest_order = await service.start_order_execution(order)
    if latest_order.status in [OrderStatus.PLACED, OrderStatus.PARTIALLY_FILLED, OrderStatus.EXECUTED]:
        latest_job = await service.db_client.update_execution_job(job.job_id, {"status": ExecutionJobStatus.SUCCEEDED, "last_error": None})
    elif latest_order.status == OrderStatus.FAILED:
        latest_job = await service.db_client.update_execution_job(job.job_id, {"status": ExecutionJobStatus.FAILED, "last_error": latest_order.reason or "Order execution failed"})
    else:
        latest_job = await service.db_client.update_execution_job(job.job_id, {"status": ExecutionJobStatus.QUEUED, "last_error": f"Order remained in status {latest_order.status}"})
    return latest_order, latest_job


def _created_batch_row(order_request: CreateOrderRequest, order: Order, job: ExecutionJob, *, processed_now: bool) -> Dict[str, Any]:
    order_payload = _order_response_payload(order)
    return {
        "symbol": order.symbol,
        "strategy_bucket": getattr(order, "strategy_bucket", getattr(order_request, "strategy_bucket", "unassigned")),
        "quantity": order.quantity,
        "final_quantity": order_request.final_quantity,
        "risk_approval_id": order_request.risk_approval_id,
        "order_id": order.order_id,
        "trade_id": order.trade_id,
        "status": str(order.status),
        "broker_order_id": order.broker_order_id,
        "reason": order.reason,
        "processed_now": processed_now,
        "order": order_payload,
        "execution_job": job.model_dump(mode="json"),
    }


@app.post("/execute", response_model=StandardAgentResponse[Dict[str, Any]], status_code=202)
@app.post("/execute_trade", response_model=StandardAgentResponse[Dict[str, Any]], status_code=202, include_in_schema=False)
async def create_order(order_request: CreateOrderRequest, service: ExecutionService = Depends(get_execution_service), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    _ensure_trading_enabled()
    order_request.trade_id = idempotency_key or order_request.trade_id
    order = await service.create_order(order_request)
    job = await service.enqueue_order_execution(order)
    return wrap_success({"order": _order_response_payload(order), "execution_job": job.model_dump(mode="json")})


@app.post("/execute/batch/validate", response_model=StandardAgentResponse[Dict[str, Any]])
async def validate_execute_batch(order_requests: List[CreateOrderRequest], adapter: BrokerAdapter = Depends(get_broker_adapter)):
    open_orders = await adapter.get_open_orders()
    result = validate_bucket_order_batch(order_requests, existing_open_symbols=_open_order_symbols(open_orders))
    return wrap_success(result, confidence_score=1.0 if result.get("approved") else 0.0)


@app.post("/execute/batch", response_model=StandardAgentResponse[Dict[str, Any]], status_code=202)
async def create_order_batch(order_requests: List[CreateOrderRequest], auto_process: Optional[bool] = None, service: ExecutionService = Depends(get_execution_service), adapter: BrokerAdapter = Depends(get_broker_adapter)):
    _ensure_trading_enabled()
    open_orders = await adapter.get_open_orders()
    validation = validate_bucket_order_batch(order_requests, existing_open_symbols=_open_order_symbols(open_orders))
    if not validation.get("approved"):
        return wrap_success({"approved": False, "created": [], "failed": [], "validation": validation, "auto_process": False}, confidence_score=0.0)

    process_now = _should_process_batch_now(auto_process)
    created: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for order_request in order_requests:
        try:
            order = await service.create_order(order_request)
            job = await service.enqueue_order_execution(order)
            if process_now:
                order, job = await _process_order_now(service, order, job)
            created.append(_created_batch_row(order_request, order, job, processed_now=process_now))
        except Exception as exc:
            failed.append({
                "symbol": order_request.symbol,
                "strategy_bucket": getattr(order_request, "strategy_bucket", "unassigned"),
                "quantity": getattr(order_request, "quantity", None),
                "final_quantity": getattr(order_request, "final_quantity", None),
                "risk_approval_id": getattr(order_request, "risk_approval_id", None),
                "reason": str(exc),
            })
    return wrap_success({
        "approved": len(failed) == 0,
        "auto_process": process_now,
        "created": created,
        "failed": failed,
        "validation": validation,
    }, confidence_score=1.0 if not failed else 0.5)


@app.get("/jobs/{job_id}", response_model=StandardAgentResponse[ExecutionJob])
async def get_execution_job(job_id: Union[int, str], service: ExecutionService = Depends(get_execution_service)):
    job = await service.get_execution_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Execution job not found")
    return wrap_success(job)


@app.post("/jobs/process-next", response_model=StandardAgentResponse[Optional[ExecutionJob]])
async def process_next_execution_job(service: ExecutionService = Depends(get_execution_service)):
    return wrap_success(await service.process_next_execution_job())


@app.post("/reconciliation/run-once", response_model=StandardAgentResponse[ReconciliationReport])
async def run_reconciliation_once(limit: int = 100, service: ExecutionService = Depends(get_execution_service)):
    return wrap_success(await service.reconcile_broker_orders(limit=limit))


@app.get("/execute/{order_id}", response_model=StandardAgentResponse[Order])
async def get_order(order_id: int, service: ExecutionService = Depends(get_execution_service)):
    order = await service.refresh_order_status(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return wrap_success(order)


@app.post("/execute/{order_id}/cancel", response_model=StandardAgentResponse[Order])
async def cancel_order(order_id: int, service: ExecutionService = Depends(get_execution_service)):
    order = await service.db_client.get_order_by_order_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in [OrderStatus.EXECUTED, OrderStatus.FAILED, OrderStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail=f"Order in state '{order.status}' cannot be cancelled.")
    if order.broker_order_id:
        cancellation_result = await service.broker_adapter.cancel_order(order.broker_order_id)
        if cancellation_result.get("status") == OrderStatus.CANCELLED:
            return wrap_success(await service.db_client.update_order(order_id, {"status": OrderStatus.CANCELLED}))
        raise HTTPException(status_code=500, detail=cancellation_result.get("message", "Broker failed to cancel the order."))
    raise HTTPException(status_code=400, detail="Order has no broker ID and cannot be cancelled.")


@app.get("/account", response_model=StandardAgentResponse[Dict[str, Any]])
async def get_account(adapter: BrokerAdapter = Depends(get_broker_adapter)):
    return wrap_success(await adapter.get_account())


@app.get("/positions", response_model=StandardAgentResponse[List[Dict[str, Any]]])
async def get_positions(adapter: BrokerAdapter = Depends(get_broker_adapter)):
    return wrap_success(await adapter.get_positions())


@app.get("/orders", response_model=StandardAgentResponse[List[Dict[str, Any]]])
@app.get("/orders/open", response_model=StandardAgentResponse[List[Dict[str, Any]]])
async def get_open_orders(adapter: BrokerAdapter = Depends(get_broker_adapter)):
    return wrap_success(await adapter.get_open_orders())


@app.get("/portfolio", response_model=StandardAgentResponse[Dict[str, Any]])
async def get_portfolio(adapter: BrokerAdapter = Depends(get_broker_adapter)):
    account = await adapter.get_account()
    positions = await adapter.get_positions()
    open_orders = await adapter.get_open_orders()
    return wrap_success({"mode": _broker_mode(), "trading_mode": _trading_mode(), "trading_enabled": settings.TRADING_ENABLED, "account": account, "positions": positions, "open_orders": open_orders, "position_count": len(positions), "open_order_count": len(open_orders)})


@app.get("/broker/preflight", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_preflight(service: ExecutionService = Depends(get_execution_service)):
    return wrap_success(await service.broker_preflight_snapshot())


@app.post("/broker/preflight/order/{order_id}", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_order_preflight(order_id: int, service: ExecutionService = Depends(get_execution_service)):
    order = await service.db_client.get_order_by_order_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return wrap_success(await service.run_broker_preflight(order))


@app.get("/broker/cleanup/status", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_cleanup_status(max_age_minutes: Optional[int] = None, service: BrokerCleanupService = Depends(get_broker_cleanup_service)):
    return wrap_success(await service.cleanup_status(max_age_minutes=max_age_minutes))


@app.post("/broker/orders/cancel-stale", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_cancel_stale_orders(dry_run: bool = True, max_age_minutes: Optional[int] = None, service: BrokerCleanupService = Depends(get_broker_cleanup_service)):
    return wrap_success(await service.cancel_stale_open_orders(max_age_minutes=max_age_minutes, dry_run=dry_run))


@app.post("/broker/orders/cancel-all-open", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_cancel_all_open_orders(dry_run: bool = True, service: BrokerCleanupService = Depends(get_broker_cleanup_service)):
    return wrap_success(await service.cancel_all_open_orders(dry_run=dry_run))


@app.get("/broker/state", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_state(account_id: int = 1, service: BrokerStateReconciliationService = Depends(get_broker_state_reconciliation_service)):
    return wrap_success(await service.collect_broker_state(account_id=account_id))


@app.post("/broker/reconcile", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_reconcile(account_id: int = 1, push_to_database: bool = True, service: BrokerStateReconciliationService = Depends(get_broker_state_reconciliation_service)):
    return wrap_success(await service.reconcile(account_id=account_id, push_to_database=push_to_database))


@app.get("/health", response_model=StandardAgentResponse[HealthResponse])
async def health(adapter: BrokerAdapter = Depends(get_broker_adapter)):
    try:
        connected = await adapter.is_connected()
    except Exception:
        connected = False
    return wrap_success(HealthResponse(status="healthy", broker_connected=connected, mode=_broker_mode()))
