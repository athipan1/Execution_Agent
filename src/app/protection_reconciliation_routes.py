from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.base import BrokerAdapter
from app.models import StandardAgentResponse
from app.services.protection_diagnostics import build_protection_diagnostics
from app.services.protection_reconciliation import build_protection_reconciliation_preview
from app.trade_plan_execution import get_broker_adapter, wrap_success

router = APIRouter()


@router.post(
    "/broker/protection/reconciliation-preview",
    response_model=StandardAgentResponse[Dict[str, Any]],
)
async def broker_protection_reconciliation_preview(
    payload: Dict[str, Any],
    adapter: BrokerAdapter = Depends(get_broker_adapter),
):
    """Build a broker-backed, read-only OCO reconciliation preview."""
    proposals = payload.get("proposals") if isinstance(payload, dict) else None
    if not isinstance(proposals, list):
        raise HTTPException(status_code=422, detail="proposals must be a list")

    positions = await adapter.get_positions()
    open_orders = await adapter.get_open_orders()
    diagnostics = build_protection_diagnostics(positions, open_orders)
    preview = build_protection_reconciliation_preview(diagnostics, proposals)
    confidence = 1.0 if preview["summary"]["blocked_count"] == 0 else 0.8
    return wrap_success(preview, confidence_score=confidence)
