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
    return {
        str(symbol).strip().upper()
        for symbol in raw_symbols
        if str(symbol or "").strip()
    }


def _ticket_plan_material(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": plan.get("symbol"),
        "qty": plan.get("position_qty"),
        "stop_price": plan.get("stop_price"),
        "take_profit_price": plan.get("take_profit_price"),
        "current_stop_order_id": (plan.get("current_stop_order") or {}).get("id"),
    }


def _ticket_id(plans: List[Dict[str, Any]], reward_risk_ratio: float) -> str:
    material = {
        "reward_risk_ratio": reward_risk_ratio,
        "plans": sorted(
            (_ticket_plan_material(plan) for plan in plans),
            key=lambda row: (
                str(row.get("symbol") or ""),
                str(row.get("current_stop_order_id") or ""),
            ),
        ),
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"order-review-{digest[:16]}"


def _non_action_plan(plan: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "preview_status": "no_action_required",
        "reason": plan.get("reason"),
        "recommended_next_step": plan.get("recommended_next_step"),
    }


def _blocked_plan(plan: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "preview_status": plan.get("preview_status"),
        "reason": plan.get("reason"),
        "recommended_next_step": plan.get("recommended_next_step"),
    }


def _next_step(
    ready: List[Dict[str, Any]],
    blocked: List[Dict[str, Any]],
) -> str:
    if ready:
        return "review_ticket_then_use_a_separate_approved_execution_workflow"
    if blocked:
        return "resolve_blockers_then_refresh_order_review_preview"
    return "no_manual_approval_required"


def _ticket_status(
    ready: List[Dict[str, Any]],
    no_action: List[Dict[str, Any]],
    blocked: List[Dict[str, Any]],
) -> str:
    if blocked:
        return "blocked"
    if ready:
        return "ready_for_manual_approval"
    if no_action:
        return "no_action_required"
    return "empty"


def build_order_review_approval_ticket(
    preview: Dict[str, Any],
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a read-only manual approval ticket from an order review preview."""
    requested_symbols = _symbol_set(payload)
    plans = preview.get("plans") or []
    ready: List[Dict[str, Any]] = []
    no_action: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []

    for plan in plans:
        if not isinstance(plan, dict):
            continue
        symbol = str(plan.get("symbol") or "").upper()
        if requested_symbols and symbol not in requested_symbols:
            continue

        preview_status = plan.get("preview_status")
        if preview_status == "ready_for_manual_review":
            ready.append(
                {
                    "symbol": symbol,
                    "position_qty": plan.get("position_qty"),
                    "current_stop_order_id": (
                        plan.get("current_stop_order") or {}
                    ).get("id"),
                    "current_stop_order": plan.get("current_stop_order"),
                    "reference_price": plan.get("reference_price"),
                    "stop_price": plan.get("stop_price"),
                    "take_profit_price": plan.get("take_profit_price"),
                    "reward_risk_ratio": plan.get("reward_risk_ratio"),
                    "proposed_actions": plan.get("proposed_actions") or [],
                    "approval_status": "manual_approval_required",
                }
            )
        elif preview_status == "no_action_required":
            no_action.append(_non_action_plan(plan, symbol))
        else:
            blocked.append(_blocked_plan(plan, symbol))

    if requested_symbols and not ready and not no_action and not blocked:
        blocked = [
            {
                "symbol": symbol,
                "preview_status": "blocked_symbol_not_found_in_preview",
                "reason": (
                    "Requested symbol was not present in the latest order "
                    "review preview."
                ),
                "recommended_next_step": (
                    "refresh_preview_or_verify_current_broker_positions"
                ),
            }
            for symbol in sorted(requested_symbols)
        ]

    ticket_status = _ticket_status(ready, no_action, blocked)
    requires_operator_attention = ticket_status in {
        "blocked",
        "ready_for_manual_approval",
    }

    return {
        "ticket_id": _ticket_id(
            ready,
            float(preview.get("reward_risk_ratio") or 2.0),
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "manual_approval_ticket",
        "safety": "read_only_no_orders_submitted_no_orders_cancelled",
        "ticket_status": ticket_status,
        "requires_operator_attention": requires_operator_attention,
        "approval_required": bool(ready),
        "execution_enabled": False,
        "manual_confirmation_phrase": "APPROVE_ORDER_REVIEW_TICKET",
        "requested_symbols": sorted(requested_symbols),
        "summary": {
            "requested_symbol_count": len(requested_symbols),
            "ready_for_manual_approval_count": len(ready),
            "no_action_required_count": len(no_action),
            "blocked_count": len(blocked),
            "requires_operator_attention": requires_operator_attention,
            "orders_submitted": False,
            "orders_cancelled": False,
        },
        "ready_for_manual_approval": ready,
        "no_action_required": no_action,
        "blocked": blocked,
        "next_step": _next_step(ready, blocked),
    }
