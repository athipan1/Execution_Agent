from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Set


def _symbol_set(payload: Dict[str, Any] | None) -> Set[str]:
    if not isinstance(payload, dict):
        return set()
    raw_symbols = payload.get("symbols") or payload.get("approved_symbols") or []
    if isinstance(raw_symbols, str):
        raw_symbols = [raw_symbols]
    if not isinstance(raw_symbols, list):
        return set()
    return {str(symbol).strip().upper() for symbol in raw_symbols if str(symbol or "").strip()}


def _ticket_id(plans: List[Dict[str, Any]], reward_risk_ratio: float) -> str:
    material = {
        "reward_risk_ratio": reward_risk_ratio,
        "plans": [
            {
                "symbol": plan.get("symbol"),
                "qty": plan.get("position_qty"),
                "stop_price": plan.get("stop_price"),
                "take_profit_price": plan.get("take_profit_price"),
                "current_stop_order_id": (plan.get("current_stop_order") or {}).get("id"),
            }
            for plan in plans
        ],
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"order-review-{digest[:16]}"


def build_order_review_approval_ticket(
    preview: Dict[str, Any],
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a read-only manual approval ticket from an order review preview.

    The ticket intentionally does not submit, cancel, or replace broker orders.
    It freezes the symbols, quantities, existing stop orders, stop prices, and
    proposed take-profit prices that a human/manual controller must review before
    any separate broker-mutation endpoint is allowed to exist.
    """
    requested_symbols = _symbol_set(payload)
    plans = preview.get("plans") or []
    ready: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []

    for plan in plans:
        if not isinstance(plan, dict):
            continue
        symbol = str(plan.get("symbol") or "").upper()
        if requested_symbols and symbol not in requested_symbols:
            continue
        if plan.get("preview_status") == "ready_for_manual_review":
            ready.append(
                {
                    "symbol": symbol,
                    "position_qty": plan.get("position_qty"),
                    "current_stop_order_id": (plan.get("current_stop_order") or {}).get("id"),
                    "current_stop_order": plan.get("current_stop_order"),
                    "reference_price": plan.get("reference_price"),
                    "stop_price": plan.get("stop_price"),
                    "take_profit_price": plan.get("take_profit_price"),
                    "reward_risk_ratio": plan.get("reward_risk_ratio"),
                    "proposed_actions": plan.get("proposed_actions") or [],
                    "approval_status": "manual_approval_required",
                }
            )
        else:
            blocked.append(
                {
                    "symbol": symbol,
                    "preview_status": plan.get("preview_status"),
                    "reason": plan.get("reason"),
                    "recommended_next_step": plan.get("recommended_next_step"),
                }
            )

    ticket = {
        "ticket_id": _ticket_id(ready, float(preview.get("reward_risk_ratio") or 2.0)),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "manual_approval_ticket",
        "safety": "read_only_no_orders_submitted_no_orders_cancelled",
        "approval_required": True,
        "execution_enabled": False,
        "manual_confirmation_phrase": "APPROVE_ORDER_REVIEW_TICKET",
        "requested_symbols": sorted(requested_symbols),
        "summary": {
            "requested_symbol_count": len(requested_symbols),
            "ready_for_manual_approval_count": len(ready),
            "blocked_count": len(blocked),
            "orders_submitted": False,
            "orders_cancelled": False,
        },
        "ready_for_manual_approval": ready,
        "blocked": blocked,
        "next_step": "review_ticket_then_use_a_separate_approved_execution_workflow",
    }

    if requested_symbols and not ready and not blocked:
        ticket["summary"]["blocked_count"] = len(requested_symbols)
        ticket["blocked"] = [
            {
                "symbol": symbol,
                "preview_status": "blocked_symbol_not_found_in_preview",
                "reason": "Requested symbol was not present in the latest order review preview.",
                "recommended_next_step": "refresh_preview_or_verify_current_broker_positions",
            }
            for symbol in sorted(requested_symbols)
        ]

    return ticket
