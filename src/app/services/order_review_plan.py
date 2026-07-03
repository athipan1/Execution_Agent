from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_REWARD_RISK_RATIO = 2.0


def _float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_price(value: float) -> float:
    return round(value, 2)


def _first_order(row: Dict[str, Any], key: str) -> Dict[str, Any]:
    orders = row.get(key) or []
    if isinstance(orders, list) and orders:
        first = orders[0]
        return first if isinstance(first, dict) else {}
    return {}


def _order_stop_price(order: Dict[str, Any]) -> Optional[float]:
    return _float(
        order.get("stop_price")
        or order.get("trigger_price")
        or order.get("price")
        or order.get("limit_price")
    )


def _reference_price(row: Dict[str, Any]) -> Optional[float]:
    return _float(
        row.get("current_price")
        or row.get("avg_entry_price")
        or row.get("average_price")
        or row.get("entry_price")
    )


def _take_profit_for_long(reference_price: float, stop_price: float, reward_risk_ratio: float) -> Optional[float]:
    risk_per_share = reference_price - stop_price
    if risk_per_share <= 0:
        return None
    return _round_price(reference_price + (risk_per_share * reward_risk_ratio))


def _build_stop_only_plan(row: Dict[str, Any], reward_risk_ratio: float) -> Dict[str, Any]:
    stop_order = _first_order(row, "protective_orders")
    stop_price = _order_stop_price(stop_order)
    reference = _reference_price(row)
    base = {
        "symbol": row.get("symbol"),
        "position_qty": row.get("position_qty"),
        "current_status": row.get("protection_status"),
        "current_action": row.get("recommended_action"),
        "current_stop_order": stop_order,
        "orders_submitted": False,
        "orders_cancelled": False,
        "preview_only": True,
    }

    if stop_price is None:
        return {
            **base,
            "preview_status": "blocked_missing_stop_price",
            "recommended_next_step": "fetch_full_broker_order_details_before_cancel_replace",
            "reason": "Existing stop-only order does not expose stop_price/trigger_price in diagnostics payload.",
            "proposed_actions": [],
        }

    if reference is None:
        return {
            **base,
            "preview_status": "blocked_missing_reference_price",
            "recommended_next_step": "fetch_position_current_price_or_average_entry_price",
            "reason": "Position does not expose current_price or avg_entry_price for take-profit calculation.",
            "proposed_actions": [],
        }

    take_profit_price = _take_profit_for_long(reference, stop_price, reward_risk_ratio)
    if take_profit_price is None:
        return {
            **base,
            "preview_status": "blocked_invalid_stop_direction",
            "recommended_next_step": "review_existing_stop_price",
            "reason": "For long positions, stop_price must be below the reference price.",
            "reference_price": reference,
            "stop_price": stop_price,
            "proposed_actions": [],
        }

    return {
        **base,
        "preview_status": "ready_for_manual_review",
        "recommended_next_step": "manual_approval_required_before_execute",
        "reference_price": reference,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "reward_risk_ratio": reward_risk_ratio,
        "proposed_actions": [
            {
                "action": "would_cancel_existing_stop_order",
                "broker_order_id": stop_order.get("id"),
                "symbol": row.get("symbol"),
            },
            {
                "action": "would_submit_bracket_replacement",
                "symbol": row.get("symbol"),
                "qty": row.get("position_qty"),
                "side": "sell",
                "stop_loss": {"stop_price": stop_price},
                "take_profit": {"limit_price": take_profit_price},
                "order_class": "bracket",
            },
        ],
    }


def build_order_review_plan(
    diagnostics: Dict[str, Any],
    *,
    reward_risk_ratio: float = DEFAULT_REWARD_RISK_RATIO,
) -> Dict[str, Any]:
    """Build a read-only preview plan from broker protection diagnostics.

    This function never submits, cancels, or replaces broker orders. It only
    describes what would be required before a future manual/approved execution
    path can safely replace stop-only protection with TP/SL bracket protection.
    """
    rows = diagnostics.get("positions") or []
    plans: List[Dict[str, Any]] = []
    summary = {
        "positions_checked": len(rows),
        "preview_only": True,
        "orders_submitted": False,
        "orders_cancelled": False,
        "candidate_count": 0,
        "ready_for_manual_review_count": 0,
        "blocked_count": 0,
        "no_action_count": 0,
    }

    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("protection_status") or "").lower()
        if status == "stop_only":
            summary["candidate_count"] += 1
            plan = _build_stop_only_plan(row, reward_risk_ratio)
            if plan.get("preview_status") == "ready_for_manual_review":
                summary["ready_for_manual_review_count"] += 1
            else:
                summary["blocked_count"] += 1
            plans.append(plan)
        elif status == "unprotected":
            summary["blocked_count"] += 1
            plans.append(
                {
                    "symbol": row.get("symbol"),
                    "position_qty": row.get("position_qty"),
                    "current_status": status,
                    "preview_status": "blocked_unprotected_position",
                    "recommended_next_step": "create_protective_order_from_risk_agent_before_any_upgrade_flow",
                    "orders_submitted": False,
                    "orders_cancelled": False,
                    "preview_only": True,
                    "proposed_actions": [],
                }
            )
        else:
            summary["no_action_count"] += 1
            plans.append(
                {
                    "symbol": row.get("symbol"),
                    "position_qty": row.get("position_qty"),
                    "current_status": status or row.get("protection_status"),
                    "preview_status": "no_action_required",
                    "recommended_next_step": "none",
                    "orders_submitted": False,
                    "orders_cancelled": False,
                    "preview_only": True,
                    "proposed_actions": [],
                }
            )

    return {
        "status": "success",
        "mode": "preview_only",
        "safety": "read_only_no_orders_submitted_no_orders_cancelled",
        "reward_risk_ratio": reward_risk_ratio,
        "summary": summary,
        "plans": plans,
    }
