from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.base import BrokerAdapter
from app.services.manual_order_review_gate import build_manual_order_review_gate
from app.services.order_review_approval_ticket import build_order_review_approval_ticket
from app.services.order_review_plan import build_order_review_plan
from app.services.protection_diagnostics import build_protection_diagnostics
from app.trade_plan_execution import _broker_mode, _trading_mode, get_broker_adapter, wrap_success
from app.models import StandardAgentResponse

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
    """Rebuild the latest broker-backed ticket without changing the reviewed id.

    The normal approval ticket is created from every currently ready item. The
    manual gate may approve only a subset of symbols, so rebuilding the ticket
    with the gate payload would produce a different subset hash and incorrectly
    fail the ticket_id check. Prefer the full current ticket; fall back to a
    symbol-scoped rebuild only when the submitted ticket was originally scoped.
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


@router.post("/broker/order-review/manual-review-gate", response_model=StandardAgentResponse[Dict[str, Any]])
async def broker_order_review_manual_review_gate(
    payload: Optional[Dict[str, Any]] = None,
    adapter: BrokerAdapter = Depends(get_broker_adapter),
):
    """Validate a manual review request against the latest broker reads."""
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
    confidence = 1.0 if gate.get("approval_valid") else 0.7
    return wrap_success(gate, confidence_score=confidence)
