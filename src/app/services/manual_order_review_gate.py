from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

CONFIRMATION_PHRASE = "APPROVE_ORDER_REVIEW_TICKET"
WORKING_STATUSES = {"new", "accepted", "pending_new", "accepted_for_bidding", "held"}


def _symbols(value: Any) -> Set[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return set()
    return {str(item).strip().upper() for item in value if str(item or "").strip()}


def _float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_equal(left: Any, right: Any) -> bool:
    left_float = _float(left)
    right_float = _float(right)
    if left_float is None or right_float is None:
        return False
    return round(left_float, 2) == round(right_float, 2)


def _text_equal(left: Any, right: Any) -> bool:
    return str(left or "").strip() == str(right or "").strip()


def _ticket_items(ticket: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in ticket.get("ready_for_manual_approval") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            result[symbol] = item
    return result


def _expected(payload: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    expected_map = payload.get("expected_orders") or {}
    if not isinstance(expected_map, dict):
        return {}
    value = expected_map.get(symbol) or expected_map.get(symbol.lower()) or {}
    return value if isinstance(value, dict) else {}


def _check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def build_manual_order_review_gate(
    *,
    payload: Dict[str, Any] | None,
    ticket: Dict[str, Any],
    account: Dict[str, Any],
    broker_mode: str,
    trading_mode: str,
) -> Dict[str, Any]:
    """Validate a manual order review request without changing broker state."""
    safe_payload = payload if isinstance(payload, dict) else {}
    approved_symbols = _symbols(safe_payload.get("symbols") or safe_payload.get("approved_symbols"))
    ticket_items = _ticket_items(ticket)

    global_checks = [
        _check("confirmation_phrase", safe_payload.get("confirmation_phrase") == CONFIRMATION_PHRASE, "confirmation phrase must match"),
        _check("ticket_id", _text_equal(safe_payload.get("ticket_id"), ticket.get("ticket_id")), "ticket_id must match current ticket"),
        _check("symbols_required", bool(approved_symbols), "at least one symbol is required"),
        _check("broker_mode", str(broker_mode or "").upper() == "ALPACA", "broker mode must be ALPACA"),
        _check("trading_mode", str(trading_mode or "").upper() == "PAPER", "manual review gate is paper-only"),
        _check("paper_account", account.get("paper") is True, "account must be a paper account"),
        _check("account_active", str(account.get("status") or "").upper() == "ACTIVE", "account must be ACTIVE"),
        _check("account_not_blocked", account.get("account_blocked") is not True, "account must not be blocked"),
        _check("trading_not_blocked", account.get("trading_blocked") is not True, "trading must not be blocked"),
    ]

    symbol_results: List[Dict[str, Any]] = []
    for symbol in sorted(approved_symbols):
        item = ticket_items.get(symbol)
        if not item:
            symbol_results.append(
                {
                    "symbol": symbol,
                    "status": "blocked_symbol_not_ready_in_ticket",
                    "valid": False,
                    "checks": [_check("symbol_ready", False, "symbol must be ready in current ticket")],
                    "orders_changed": False,
                }
            )
            continue

        expected = _expected(safe_payload, symbol)
        current = item.get("current_stop_order") or {}
        checks = [
            _check("expected_present", bool(expected), "expected_orders entry is required"),
            _check("qty_matches", bool(expected) and _text_equal(expected.get("qty"), item.get("position_qty")), "qty must match ticket"),
            _check("current_order_id_matches", bool(expected) and _text_equal(expected.get("current_stop_order_id"), item.get("current_stop_order_id")), "current order id must match ticket"),
            _check("stop_price_matches", bool(expected) and _price_equal(expected.get("stop_price"), item.get("stop_price")), "stop price must match ticket"),
            _check("take_profit_price_matches", bool(expected) and _price_equal(expected.get("take_profit_price"), item.get("take_profit_price")), "take profit price must match ticket"),
            _check("current_order_symbol_matches", str(current.get("symbol") or "").upper() == symbol, "current order symbol must match"),
            _check("current_order_working", str(current.get("status") or "").lower() in WORKING_STATUSES, "current order must still be working"),
        ]
        valid = all(check["passed"] for check in checks)
        symbol_results.append(
            {
                "symbol": symbol,
                "status": "validated_for_manual_review" if valid else "blocked_validation_failed",
                "valid": valid,
                "qty": item.get("position_qty"),
                "current_stop_order_id": item.get("current_stop_order_id"),
                "stop_price": item.get("stop_price"),
                "take_profit_price": item.get("take_profit_price"),
                "checks": checks,
                "orders_changed": False,
            }
        )

    valid = all(check["passed"] for check in global_checks) and bool(symbol_results) and all(item.get("valid") for item in symbol_results)
    return {
        "status": "validated" if valid else "blocked",
        "mode": "manual_order_review_gate",
        "safety": "paper_only_validation_no_broker_state_change",
        "approval_valid": valid,
        "execution_enabled": False,
        "ticket_id": ticket.get("ticket_id"),
        "requested_symbols": sorted(approved_symbols),
        "summary": {
            "requested_symbol_count": len(approved_symbols),
            "validated_symbol_count": sum(1 for item in symbol_results if item.get("valid") is True),
            "blocked_symbol_count": sum(1 for item in symbol_results if item.get("valid") is not True),
            "orders_changed": False,
        },
        "global_checks": global_checks,
        "symbols": symbol_results,
        "next_step": "review_validated_gate_before_any_separate_paper_workflow" if valid else "fix_blocked_checks_and_regenerate_ticket",
    }
