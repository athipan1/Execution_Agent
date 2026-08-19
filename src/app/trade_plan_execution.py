from __future__ import annotations

import math
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from app.adapters.alpaca_hydrated import HydratedAlpacaAdapter
from app.adapters.base import BrokerAdapter
from app.adapters.simulator import SimulatorAdapter
from app.config import settings
from app.db_client import get_db_client
from app.models import (
    CreateOrderResponse,
    ExecutionJob,
    OrderType,
    StandardAgentResponse,
    TradePlanExecutionRequest,
)
from app.services.execution_service import ExecutionService
from app.services.order_review_approval_ticket import build_order_review_approval_ticket
from app.services.order_review_plan import build_order_review_plan
from app.services.protection_diagnostics import build_protection_diagnostics

router = APIRouter()

EXECUTION_COST_CONTEXT_SCHEMA_VERSION = "execution-cost-context.v1"


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


def _should_process_batch_now(auto_process: Optional[bool]) -> bool:
    if auto_process is not None:
        return bool(auto_process) and _trading_mode() == "PAPER"
    return _trading_mode() == "PAPER"


def get_broker_adapter() -> BrokerAdapter:
    broker_mode = _validate_broker_mode()
    if broker_mode == "ALPACA":
        return HydratedAlpacaAdapter()
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


def _optional_non_negative_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _execution_cost_context(trade_plan: TradePlanExecutionRequest) -> Dict[str, Any]:
    """Build canonical decision-time context without changing order semantics.

    The context is deliberately derived from validated TradePlan fields after any
    caller metadata has been received, so a caller cannot overwrite canonical
    decision price, strategy, correlation, or lifecycle identity. Missing market
    evidence stays missing instead of being synthesized.
    """

    metadata = trade_plan.metadata if isinstance(trade_plan.metadata, dict) else {}
    regime = metadata.get("regime") or metadata.get("market_regime") or "unknown"
    spread_bps = _optional_non_negative_float(
        metadata.get("spread_bps_at_decision", metadata.get("spread_bps"))
    )
    decision_price = trade_plan.entry_price or trade_plan.limit_price
    submitted_price = (
        trade_plan.limit_price if trade_plan.order_type == OrderType.LIMIT else None
    )
    return {
        "schema_version": EXECUTION_COST_CONTEXT_SCHEMA_VERSION,
        "trade_plan_id": trade_plan.plan_id,
        "correlation_id": trade_plan.correlation_id,
        "source": trade_plan.source,
        "strategy": trade_plan.strategy.strip().lower() or "unassigned",
        "strategy_bucket": trade_plan.strategy_bucket,
        "regime": str(regime).strip().lower() or "unknown",
        "decision_price": decision_price,
        "submitted_price": submitted_price,
        "spread_bps_at_decision": spread_bps,
        "paper_calibration_eligible": _trading_mode() == "PAPER",
    }


def _attach_execution_cost_context(
    trade_plan: TradePlanExecutionRequest,
    order_request,
):
    """Attach immutable canonical telemetry to order metadata before persistence."""

    metadata = dict(order_request.metadata or {})
    metadata["execution_cost_context"] = _execution_cost_context(trade_plan)
    order_request.metadata = metadata
    return order_request


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
            "execution_cost_context": (order_request.metadata or {}).get(
                "execution_cost_context"
            ),
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
    the TradePlan into the established CreateOrderRequest contract. Canonical
    decision-time execution-cost context is added to metadata only; broker order
    type, price, quantity, protection, and mutation behavior are unchanged.
    """
    _ensure_trading_enabled()
    order_request = trade_plan.to_order_request()
    order_request = _attach_execution_cost_context(trade_plan, order_request)
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


@router.post("/broker/order-review/preview", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_order_review_preview(
    payload: Optional[Dict[str, Any]] = None,
    adapter: BrokerAdapter = Depends(get_broker_adapter),
):
    """Build a read-only preview plan for legacy stop-only protection.

    This endpoint never cancels, replaces, or submits broker orders. It only
    returns the steps that would require manual review before a future approved
    execute path can be added.
    """
    reward_risk_ratio = 2.0
    if isinstance(payload, dict) and payload.get("reward_risk_ratio") is not None:
        try:
            reward_risk_ratio = float(payload["reward_risk_ratio"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="reward_risk_ratio must be a number")
        if reward_risk_ratio <= 0:
            raise HTTPException(status_code=422, detail="reward_risk_ratio must be greater than zero")

    positions = await adapter.get_positions()
    open_orders = await adapter.get_open_orders()
    diagnostics = build_protection_diagnostics(positions, open_orders)
    preview = build_order_review_plan(diagnostics, reward_risk_ratio=reward_risk_ratio)
    needs_attention = preview["summary"]["candidate_count"] + preview["summary"]["blocked_count"]
    confidence = 1.0 if needs_attention == 0 else 0.7
    return wrap_success(preview, confidence_score=confidence)


@router.post("/broker/order-review/manual-approval-ticket", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_order_review_manual_approval_ticket(
    payload: Optional[Dict[str, Any]] = None,
    adapter: BrokerAdapter = Depends(get_broker_adapter),
):
    """Build a read-only manual approval ticket from the latest broker preview.

    This endpoint freezes the proposed stop-only upgrade details for manual
    review. It does not submit, cancel, replace, or modify broker orders.
    """
    reward_risk_ratio = 2.0
    if isinstance(payload, dict) and payload.get("reward_risk_ratio") is not None:
        try:
            reward_risk_ratio = float(payload["reward_risk_ratio"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="reward_risk_ratio must be a number")
        if reward_risk_ratio <= 0:
            raise HTTPException(status_code=422, detail="reward_risk_ratio must be greater than zero")

    positions = await adapter.get_positions()
    open_orders = await adapter.get_open_orders()
    diagnostics = build_protection_diagnostics(positions, open_orders)
    preview = build_order_review_plan(diagnostics, reward_risk_ratio=reward_risk_ratio)
    ticket = build_order_review_approval_ticket(preview, payload if isinstance(payload, dict) else None)
    needs_attention = ticket["summary"]["ready_for_manual_approval_count"] + ticket["summary"]["blocked_count"]
    confidence = 1.0 if needs_attention == 0 else 0.7
    return wrap_success(ticket, confidence_score=confidence)
