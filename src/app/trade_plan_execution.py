from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.adapters.alpaca import AlpacaAdapter
from app.adapters.base import BrokerAdapter
from app.adapters.simulator import SimulatorAdapter
from app.config import settings
from app.db_client import get_db_client
from app.models import (
    CreateOrderResponse,
    ExecutionJob,
    StandardAgentResponse,
    TradePlanExecutionRequest,
)
from app.services.execution_service import ExecutionService
from app.services.protection_diagnostics import build_protection_diagnostics

router = APIRouter()


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
        raise HTTPException(
            status_code=423,
            detail="Trading is disabled by TRADING_ENABLED=false.",
        )


def get_broker_adapter() -> BrokerAdapter:
    broker_mode = _validate_broker_mode()
    if broker_mode == "ALPACA":
        return AlpacaAdapter()
    return SimulatorAdapter()


def get_execution_service(
    broker_adapter: BrokerAdapter = Depends(get_broker_adapter),
) -> ExecutionService:
    return ExecutionService(get_db_client(), broker_adapter)


def wrap_success(data: Any, confidence_score: float = 1.0) -> StandardAgentResponse[Any]:
    return StandardAgentResponse(
        status="success",
        data=data,
        confidence_score=confidence_score,
    )


def _order_response_payload(order) -> Dict[str, Any]:
    return CreateOrderResponse.model_validate(order).model_dump(mode="json")


def _execution_payload(
    *,
    trade_plan: TradePlanExecutionRequest,
    order,
    job: ExecutionJob,
    order_request,
) -> Dict[str, Any]:
    return {
        "trade_plan_id": trade_plan.plan_id,
        "correlation_id": trade_plan.correlation_id,
        "source": trade_plan.source,
        "strategy": trade_plan.strategy,
        "strategy_bucket": trade_plan.strategy_bucket,
        "risk_approval_id": trade_plan.risk_approval_id,
        "order_request": {
            "trade_id": order_request.trade_id,
            "symbol": order_request.symbol,
            "side": str(order_request.side),
            "order_type": str(order_request.order_type),
            "quantity": order_request.quantity,
            "final_quantity": order_request.final_quantity,
            "price": order_request.price,
            "has_guard_plan": bool(order_request.guard_plan),
            "has_protective_exit": bool(order_request.protective_exit),
        },
        "order": _order_response_payload(order),
        "execution_job": job.model_dump(mode="json"),
    }


@router.post("/execute/trade-plan", response_model=StandardAgentResponse[Dict[str, Any]], status_code=202)
async def create_order_from_trade_plan(
    trade_plan: TradePlanExecutionRequest,
    service: ExecutionService = Depends(get_execution_service),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Create an execution order from a risk-approved TradePlan.

    This reuses the existing ExecutionService create/enqueue path after converting
    the TradePlan into the established CreateOrderRequest contract.
    """
    _ensure_trading_enabled()
    order_request = trade_plan.to_order_request()
    order_request.trade_id = idempotency_key or order_request.trade_id
    order = await service.create_order(order_request)
    job = await service.enqueue_order_execution(order)
    return wrap_success(
        _execution_payload(
            trade_plan=trade_plan,
            order=order,
            job=job,
            order_request=order_request,
        )
    )


@router.get("/broker/protection-diagnostics", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_protection_diagnostics(
    adapter: BrokerAdapter = Depends(get_broker_adapter),
):
    """Report protection quality for current broker positions.

    This endpoint is intentionally read-only. It does not cancel orders, replace
    orders, submit orders, or modify broker/database state.
    """
    positions = await adapter.get_positions()
    open_orders = await adapter.get_open_orders()
    diagnostics = build_protection_diagnostics(positions, open_orders)
    needs_attention = diagnostics["summary"]["needs_bracket_upgrade_count"] + diagnostics["summary"]["unprotected_position_count"]
    confidence = 1.0 if needs_attention == 0 else 0.7
    return wrap_success(diagnostics, confidence_score=confidence)
