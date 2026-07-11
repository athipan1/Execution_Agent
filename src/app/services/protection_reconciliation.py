from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

SUPPORTED_STATUSES = {"partially_protected", "unprotected", "stop_only"}


def _float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _proposal_map(proposals: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        symbol = _symbol(proposal.get("symbol"))
        if symbol:
            result[symbol] = proposal
    return result


def _active_order_ids(row: Mapping[str, Any]) -> List[str]:
    ids: List[str] = []
    for order in row.get("open_orders") or []:
        if not isinstance(order, dict):
            continue
        order_id = order.get("id") or order.get("broker_order_id")
        if order_id not in (None, ""):
            ids.append(str(order_id))
    return sorted(set(ids))


def _blocked(row: Mapping[str, Any], *, status: str, reason: str, next_step: str) -> Dict[str, Any]:
    return {
        "symbol": _symbol(row.get("symbol")),
        "position_qty": row.get("position_qty"),
        "current_status": row.get("protection_status"),
        "preview_status": status,
        "reason": reason,
        "recommended_next_step": next_step,
        "orders_submitted": False,
        "orders_cancelled": False,
        "preview_only": True,
        "proposed_actions": [],
    }


def build_protection_reconciliation_preview(
    diagnostics: Dict[str, Any],
    proposals: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a read-only full-position OCO reconciliation preview."""
    rows = diagnostics.get("positions") or []
    proposals_by_symbol = _proposal_map(proposals)
    plans: List[Dict[str, Any]] = []
    ready_count = 0
    blocked_count = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _symbol(row.get("symbol"))
        status = str(row.get("protection_status") or "").lower()
        if status not in SUPPORTED_STATUSES:
            continue

        proposal = proposals_by_symbol.get(symbol)
        if proposal is None:
            blocked_count += 1
            plans.append(_blocked(
                row,
                status="blocked_missing_risk_proposal",
                reason="No SL/TP proposal was supplied for this broker position.",
                next_step="request_protection_plan_from_risk_agent",
            ))
            continue

        position_qty = _float(row.get("position_qty"))
        current_price = _float(row.get("current_price") or row.get("avg_entry_price"))
        proposal_qty = _float(proposal.get("qty") or proposal.get("position_qty"))
        stop_price = _float(proposal.get("stop_price") or proposal.get("stop_loss"))
        take_profit_price = _float(proposal.get("take_profit_price") or proposal.get("take_profit"))

        if position_qty is None or position_qty <= 0:
            blocked_count += 1
            plans.append(_blocked(row, status="blocked_invalid_position_quantity", reason="Broker position quantity is missing or non-positive.", next_step="refresh_broker_positions"))
            continue
        if proposal_qty is not None and proposal_qty != position_qty:
            blocked_count += 1
            plans.append(_blocked(row, status="blocked_risk_quantity_mismatch", reason="Risk proposal quantity does not match the full broker position.", next_step="regenerate_risk_proposal_for_full_position"))
            continue
        if current_price is None or current_price <= 0:
            blocked_count += 1
            plans.append(_blocked(row, status="blocked_missing_reference_price", reason="Broker position does not expose a usable reference price.", next_step="refresh_broker_position_prices"))
            continue
        if stop_price is None or take_profit_price is None:
            blocked_count += 1
            plans.append(_blocked(row, status="blocked_incomplete_risk_proposal", reason="Risk proposal must provide both stop and take-profit prices.", next_step="regenerate_complete_risk_proposal"))
            continue
        if not (stop_price < current_price < take_profit_price):
            blocked_count += 1
            plans.append(_blocked(row, status="blocked_invalid_price_direction", reason="For a long position, stop < reference price < take profit is required.", next_step="review_risk_price_levels"))
            continue

        order_ids = _active_order_ids(row)
        ready_count += 1
        plans.append({
            "symbol": symbol,
            "position_qty": row.get("position_qty"),
            "current_status": status,
            "preview_status": "ready_for_manual_reconciliation_review",
            "recommended_next_step": "manual_approval_required_before_reconciliation",
            "reference_price": current_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "risk_policy_version": proposal.get("risk_policy_version"),
            "calculation_method": proposal.get("calculation_method"),
            "existing_open_order_ids": order_ids,
            "orders_submitted": False,
            "orders_cancelled": False,
            "preview_only": True,
            "proposed_actions": [
                *[
                    {"action": "would_cancel_existing_open_order", "broker_order_id": order_id, "symbol": symbol}
                    for order_id in order_ids
                ],
                {
                    "action": "would_submit_full_position_oco",
                    "symbol": symbol,
                    "qty": row.get("position_qty"),
                    "side": "sell",
                    "order_class": "oco",
                    "stop_loss": {"stop_price": stop_price},
                    "take_profit": {"limit_price": take_profit_price},
                },
                {"action": "would_verify_full_position_protection", "symbol": symbol, "expected_qty": row.get("position_qty")},
            ],
        })

    eligible_count = sum(
        1 for row in rows
        if isinstance(row, dict) and str(row.get("protection_status") or "").lower() in SUPPORTED_STATUSES
    )
    return {
        "status": "success",
        "mode": "reconciliation_preview_only",
        "safety": "read_only_no_orders_submitted_no_orders_cancelled",
        "summary": {
            "positions_checked": len(rows),
            "eligible_position_count": eligible_count,
            "ready_for_manual_review_count": ready_count,
            "blocked_count": blocked_count,
            "orders_submitted": False,
            "orders_cancelled": False,
        },
        "plans": plans,
    }
