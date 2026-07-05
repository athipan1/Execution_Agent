from __future__ import annotations

import math
import uuid
from typing import Any, Dict, List

from app.models import CreateOrderRequest, OrderSide, PortfolioExecutionRequest, PortfolioRiskApproval


def _risk_response(approval: PortfolioRiskApproval) -> Dict[str, Any]:
    return approval.risk_response or approval.risk or {}


def _approved_quantity(approval: PortfolioRiskApproval) -> int:
    value = approval.final_quantity
    if value is None:
        value = approval.approved_quantity
    if value is None:
        risk_response = _risk_response(approval)
        value = risk_response.get("final_quantity") or risk_response.get("approved_quantity")
    try:
        return int(math.floor(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _risk_approval_id(approval: PortfolioRiskApproval) -> str:
    risk_response = _risk_response(approval)
    value = approval.risk_approval_id or risk_response.get("risk_approval_id") or risk_response.get("approval_id")
    return str(value or "").strip()


def _guard_plan(approval: PortfolioRiskApproval) -> Dict[str, Any] | None:
    risk_response = _risk_response(approval)
    return approval.guard_plan or risk_response.get("guard_plan")


def _protective_exit(approval: PortfolioRiskApproval) -> Dict[str, Any] | None:
    risk_response = _risk_response(approval)
    return approval.protective_exit or risk_response.get("protective_exit")


def _metadata(approval: PortfolioRiskApproval) -> Dict[str, Any]:
    """Merge metadata from Risk response and approval.

    Manager may attach Curator/skill telemetry either directly on the approval
    or inside the nested risk response metadata. Keep both paths so portfolio
    execution can preserve skill_id and skill_execution_log_id for later
    Database_Agent /skills/trade-outcomes reporting.
    """
    risk_response = _risk_response(approval)
    metadata: Dict[str, Any] = {}

    risk_metadata = risk_response.get("metadata")
    if isinstance(risk_metadata, dict):
        metadata.update(risk_metadata)

    approval_metadata = getattr(approval, "metadata", None)
    if isinstance(approval_metadata, dict):
        metadata.update(approval_metadata)

    return metadata


def _price_for_approval(request: PortfolioExecutionRequest, approval: PortfolioRiskApproval) -> float | None:
    symbol = str(approval.symbol).upper()
    if symbol in request.price_by_symbol:
        return request.price_by_symbol[symbol]

    risk_response = _risk_response(approval)
    for key in ("entry_price", "price", "approved_price"):
        value = risk_response.get(key)
        if value is not None:
            return float(value)

    return request.default_price


def _side_for_approval(request: PortfolioExecutionRequest, approval: PortfolioRiskApproval) -> OrderSide:
    symbol = str(approval.symbol).upper()
    if symbol in request.side_by_symbol:
        return request.side_by_symbol[symbol]

    risk_response = _risk_response(approval)
    side = risk_response.get("side") or risk_response.get("action")
    if side:
        side_text = str(side).lower()
        return OrderSide.SELL if "sell" in side_text else OrderSide.BUY

    return request.default_side


def build_order_requests_from_portfolio(request: PortfolioExecutionRequest) -> tuple[List[CreateOrderRequest], List[Dict[str, Any]]]:
    """Convert Risk_Agent portfolio approvals into executable order requests.

    This is intentionally strict: only approved approvals with a positive final quantity,
    a risk approval id, and a guard/protective exit are converted. Rejected rows are
    returned in failed_to_build so Manager can explain why a portfolio order was not
    submitted.
    """
    orders: List[CreateOrderRequest] = []
    failed_to_build: List[Dict[str, Any]] = []

    for index, approval in enumerate(request.approvals):
        symbol = str(approval.symbol).upper()
        if not approval.approved:
            failed_to_build.append({"symbol": symbol, "reason": "risk_approval_not_approved"})
            continue

        quantity = _approved_quantity(approval)
        if quantity <= 0:
            failed_to_build.append({"symbol": symbol, "reason": "missing_positive_final_quantity"})
            continue

        risk_approval_id = _risk_approval_id(approval)
        if not risk_approval_id:
            failed_to_build.append({"symbol": symbol, "reason": "missing_risk_approval_id"})
            continue

        guard_plan = _guard_plan(approval)
        protective_exit = _protective_exit(approval)
        if not guard_plan and not protective_exit:
            failed_to_build.append({"symbol": symbol, "reason": "missing_guard_plan_or_protective_exit"})
            continue

        try:
            order = CreateOrderRequest(
                trade_id=f"{request.trade_id_prefix}-{symbol}-{index + 1}-{uuid.uuid4().hex[:8]}",
                account_id=request.account_id,
                symbol=symbol,
                side=_side_for_approval(request, approval),
                order_type=request.order_type,
                price=_price_for_approval(request, approval),
                quantity=quantity,
                time_in_force=request.time_in_force,
                strategy_bucket=approval.strategy_bucket,
                risk_approval_id=risk_approval_id,
                final_quantity=quantity,
                guard_plan=guard_plan,
                protective_exit=protective_exit,
                metadata=_metadata(approval),
            )
            orders.append(order)
        except Exception as exc:
            failed_to_build.append({"symbol": symbol, "reason": str(exc)})

    return orders, failed_to_build
