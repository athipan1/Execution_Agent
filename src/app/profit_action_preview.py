from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.adapters.base import BrokerAdapter
from app.models import StandardAgentResponse
from app.services.approved_bracket_upgrade_executor import execute_approved_paper_bracket_upgrade
from app.services.manual_order_review_gate import build_manual_order_review_gate
from app.services.order_review_approval_ticket import build_order_review_approval_ticket
from app.services.order_review_plan import build_order_review_plan
from app.services.protection_diagnostics import build_protection_diagnostics
from app.trade_plan_execution import _broker_mode, _trading_mode, get_broker_adapter

ProfitAction = Literal["hold", "move_stop", "partial_exit", "exit_all"]
PositionSide = Literal["long", "short"]


class ProfitPreviewPosition(BaseModel):
    symbol: str
    side: PositionSide = "long"
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    current_price: float = Field(gt=0)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    strategy_bucket: str = "unassigned"


class ProfitPreviewAction(BaseModel):
    action: ProfitAction
    symbol: str
    quantity: float = Field(ge=0, default=0)
    recommended_stop: Optional[float] = Field(default=None, gt=0)
    reason: Optional[str] = None
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProfitPreviewRiskResult(BaseModel):
    approved: bool = False
    status: Optional[str] = None
    approved_actions: List[Dict[str, Any]] = Field(default_factory=list)
    rejected_actions: List[Dict[str, Any]] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ProfitActionPreviewRequest(BaseModel):
    position: ProfitPreviewPosition
    action: ProfitPreviewAction
    risk_result: ProfitPreviewRiskResult
    dry_run: bool = True
    manual_approval_required: bool = True

    @model_validator(mode="after")
    def validate_symbol_consistency(self) -> "ProfitActionPreviewRequest":
        if self.position.symbol.upper() != self.action.symbol.upper():
            raise ValueError("position symbol and action symbol must match")
        return self


router = APIRouter()


def _payload_ticket_id(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    ticket_id = payload.get("ticket_id")
    if ticket_id in (None, ""):
        return None
    return str(ticket_id).strip() or None


def _current_ticket_for_gate(
    preview: Dict[str, Any],
    payload: Optional[Dict[str, Any]],
    *,
    reward_risk_ratio: float,
) -> Dict[str, Any]:
    """Rebuild the latest broker-backed ticket without changing its scope.

    The manual gate may approve only a subset of symbols from a full approval
    ticket. Rebuilding the current ticket with the subset payload changes the
    ticket hash and incorrectly blocks an otherwise valid request. Prefer the
    full current ticket; fall back to a symbol-scoped ticket only when the
    submitted ticket id was originally scoped.
    """
    base_payload = {"reward_risk_ratio": reward_risk_ratio}
    full_ticket = build_order_review_approval_ticket(preview, base_payload)
    requested_ticket_id = _payload_ticket_id(payload)

    if not requested_ticket_id or full_ticket.get("ticket_id") == requested_ticket_id:
        return full_ticket

    scoped_ticket = build_order_review_approval_ticket(preview, payload if isinstance(payload, dict) else None)
    if scoped_ticket.get("ticket_id") == requested_ticket_id:
        return scoped_ticket

    return full_ticket


def _preview_for_approved_action(payload: ProfitActionPreviewRequest) -> Dict[str, Any]:
    action = payload.action.action
    symbol = payload.position.symbol.upper()
    if action == "hold":
        return {
            "execution_action": "no_op",
            "symbol": symbol,
            "quantity": 0,
            "order_preview": None,
            "reason": payload.action.reason or "Risk-approved hold action; no execution required.",
        }
    if action == "move_stop":
        return {
            "execution_action": "replace_or_create_protective_stop_preview",
            "symbol": symbol,
            "quantity": payload.position.quantity,
            "order_preview": {
                "side": "sell" if payload.position.side == "long" else "buy",
                "order_type": "stop",
                "stop_price": payload.action.recommended_stop,
                "time_in_force": "GTC",
                "reduce_only_intent": True,
            },
            "reason": payload.action.reason or "Risk-approved stop adjustment preview.",
        }
    if action == "partial_exit":
        return {
            "execution_action": "partial_exit_preview",
            "symbol": symbol,
            "quantity": payload.action.quantity,
            "order_preview": {
                "side": "sell" if payload.position.side == "long" else "buy",
                "order_type": "market",
                "quantity": payload.action.quantity,
                "time_in_force": "GTC",
                "reduce_only_intent": True,
            },
            "reason": payload.action.reason or "Risk-approved partial exit preview.",
        }
    return {
        "execution_action": "exit_all_preview_manual_approval_required",
        "symbol": symbol,
        "quantity": payload.position.quantity,
        "order_preview": {
            "side": "sell" if payload.position.side == "long" else "buy",
            "order_type": "market",
            "quantity": payload.position.quantity,
            "time_in_force": "GTC",
            "reduce_only_intent": True,
        },
        "reason": payload.action.reason or "Exit-all preview only; manual approval remains required.",
    }


def build_profit_action_preview(payload: ProfitActionPreviewRequest) -> Dict[str, Any]:
    risk_approved = bool(payload.risk_result.approved)
    blocked_reasons: List[str] = []
    if not payload.dry_run:
        blocked_reasons.append("profit_action_preview_requires_dry_run")
    if not risk_approved:
        blocked_reasons.append("risk_gate_not_approved")
    if payload.action.action == "exit_all" and payload.manual_approval_required:
        blocked_reasons.append("exit_all_manual_approval_required")

    approved_for_execution = not blocked_reasons
    preview = _preview_for_approved_action(payload) if risk_approved else None
    return {
        "approved_for_execution": approved_for_execution,
        "dry_run": True,
        "orders_submitted": False,
        "symbol": payload.position.symbol.upper(),
        "profit_action": payload.action.action,
        "risk_approved": risk_approved,
        "manual_approval_required": payload.manual_approval_required,
        "blocked_reasons": blocked_reasons,
        "preview": preview,
        "risk_result": payload.risk_result.model_dump(mode="json"),
        "safety": "preview_only_no_orders_submitted",
    }


@router.post("/execution/profit-action-preview", response_model=StandardAgentResponse[Dict[str, Any]])
async def profit_action_preview(payload: ProfitActionPreviewRequest):
    result = build_profit_action_preview(payload)
    return StandardAgentResponse(
        status="success",
        data=result,
        confidence_score=1.0 if result["risk_approved"] else 0.0,
    )


async def _latest_gate_from_payload(payload: Optional[Dict[str, Any]], adapter: BrokerAdapter) -> tuple[Dict[str, Any], Dict[str, Any]]:
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
    account = await adapter.get_account()
    diagnostics = build_protection_diagnostics(positions, open_orders)
    preview = build_order_review_plan(diagnostics, reward_risk_ratio=reward_risk_ratio)
    ticket = _current_ticket_for_gate(preview, payload, reward_risk_ratio=reward_risk_ratio)
    gate = build_manual_order_review_gate(
        payload=payload,
        ticket=ticket,
        account=account,
        broker_mode=_broker_mode(),
        trading_mode=_trading_mode(),
    )
    return gate, account


@router.post("/broker/order-review/manual-review-gate", response_model=StandardAgentResponse[Dict[str, Any]])
async def manual_order_review_gate(
    payload: Optional[Dict[str, Any]] = None,
    adapter: BrokerAdapter = Depends(get_broker_adapter),
):
    gate, _account = await _latest_gate_from_payload(payload, adapter)
    return StandardAgentResponse(
        status="success",
        data=gate,
        confidence_score=1.0 if gate.get("approval_valid") else 0.7,
    )


@router.post("/broker/order-review/execute-approved-bracket-upgrade", response_model=StandardAgentResponse[Dict[str, Any]])
async def execute_approved_bracket_upgrade(
    payload: Optional[Dict[str, Any]] = None,
    adapter: BrokerAdapter = Depends(get_broker_adapter),
):
    gate, account = await _latest_gate_from_payload(payload, adapter)
    result = await execute_approved_paper_bracket_upgrade(
        payload=payload,
        gate=gate,
        account=account,
        adapter=adapter,
        broker_mode=_broker_mode(),
        trading_mode=_trading_mode(),
    )
    return StandardAgentResponse(
        status="success" if result.get("status") in {"executed", "blocked"} else "error",
        data=result,
        confidence_score=1.0 if result.get("status") == "executed" else 0.7,
    )