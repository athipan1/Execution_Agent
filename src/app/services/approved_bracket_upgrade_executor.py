from __future__ import annotations

from typing import Any, Dict, List

EXECUTION_CONFIRMATION_PHRASE = "EXECUTE_PAPER_BRACKET_UPGRADE"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _cancel_succeeded(result: Dict[str, Any]) -> bool:
    return _status(result.get("status")) in {"cancelled", "canceled", "success"}


def _submit_succeeded(result: Dict[str, Any]) -> bool:
    return _status(result.get("status")) in {"placed", "accepted", "new", "success"} or bool(result.get("broker_order_id"))


def _validated_symbols(gate: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in gate.get("symbols") or [] if isinstance(item, dict) and item.get("valid") is True]


async def execute_approved_paper_bracket_upgrade(
    *,
    payload: Dict[str, Any] | None,
    gate: Dict[str, Any],
    account: Dict[str, Any],
    adapter: Any,
    broker_mode: str,
    trading_mode: str,
) -> Dict[str, Any]:
    """Cancel one validated stop-only paper order and replace it with an exit TP/SL OCO order.

    This function is intentionally narrow and defensive:
    - PAPER only.
    - ALPACA only.
    - one symbol per request by default.
    - requires the separate execution confirmation phrase.
    - tries to restore the original stop order if the replacement submit fails.
    """
    safe_payload = payload if isinstance(payload, dict) else {}
    validated = _validated_symbols(gate)
    allow_multi_symbol = safe_payload.get("allow_multi_symbol") is True

    global_checks = [
        _check("gate_validated", gate.get("status") == "validated" and gate.get("approval_valid") is True, "manual review gate must be validated"),
        _check("execution_confirmation_phrase", safe_payload.get("execution_confirmation_phrase") == EXECUTION_CONFIRMATION_PHRASE, "execution confirmation phrase must match"),
        _check("execute_paper", safe_payload.get("execute_paper") is True, "execute_paper must be true"),
        _check("broker_mode", str(broker_mode or "").upper() == "ALPACA", "broker mode must be ALPACA"),
        _check("trading_mode", str(trading_mode or "").upper() == "PAPER", "executor is paper-only"),
        _check("paper_account", account.get("paper") is True, "account must be paper"),
        _check("account_active", str(account.get("status") or "").upper() == "ACTIVE", "account must be ACTIVE"),
        _check("account_not_blocked", account.get("account_blocked") is not True, "account must not be blocked"),
        _check("trading_not_blocked", account.get("trading_blocked") is not True, "trading must not be blocked"),
        _check("validated_symbols_present", bool(validated), "at least one validated symbol is required"),
        _check("single_symbol_default", allow_multi_symbol or len(validated) <= 1, "multi-symbol execution requires allow_multi_symbol=true"),
    ]

    if not all(check["passed"] for check in global_checks):
        return {
            "status": "blocked",
            "mode": "approved_paper_bracket_upgrade_executor",
            "safety": "paper_only_order_mutation_blocked_before_broker_call",
            "execution_enabled": False,
            "orders_changed": False,
            "ticket_id": gate.get("ticket_id"),
            "global_checks": global_checks,
            "symbols": [],
            "next_step": "fix_blocked_checks_and_revalidate_gate",
        }

    symbol_results: List[Dict[str, Any]] = []
    for item in validated:
        symbol = _text(item.get("symbol")).upper()
        qty = _text(item.get("qty"))
        current_stop_order_id = _text(item.get("current_stop_order_id"))
        stop_price = item.get("stop_price")
        take_profit_price = item.get("take_profit_price")
        actions: List[Dict[str, Any]] = []

        cancel_result = await adapter.cancel_order(current_stop_order_id)
        actions.append({"action": "cancel_existing_stop_order", "broker_order_id": current_stop_order_id, "result": cancel_result})

        if not _cancel_succeeded(cancel_result):
            symbol_results.append(
                {
                    "symbol": symbol,
                    "status": "blocked_cancel_failed",
                    "valid": False,
                    "orders_changed": False,
                    "rollback_attempted": False,
                    "actions": actions,
                }
            )
            continue

        submit_result = await adapter.submit_exit_bracket_order(
            symbol=symbol,
            qty=qty,
            side="sell",
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            client_order_id=f"manual-bracket-{gate.get('ticket_id')}-{symbol}"[:48],
        )
        actions.append({"action": "submit_exit_oco_bracket_replacement", "symbol": symbol, "result": submit_result})

        if _submit_succeeded(submit_result):
            symbol_results.append(
                {
                    "symbol": symbol,
                    "status": "executed_paper_bracket_upgrade",
                    "valid": True,
                    "qty": qty,
                    "old_stop_order_id": current_stop_order_id,
                    "new_bracket_order_id": submit_result.get("broker_order_id"),
                    "stop_price": stop_price,
                    "take_profit_price": take_profit_price,
                    "orders_changed": True,
                    "rollback_attempted": False,
                    "actions": actions,
                }
            )
            continue

        restore_result = await adapter.submit_protective_stop_order(
            symbol=symbol,
            qty=qty,
            side="sell",
            stop_price=stop_price,
            client_order_id=f"restore-stop-{gate.get('ticket_id')}-{symbol}"[:48],
        )
        actions.append({"action": "rollback_restore_stop_order", "symbol": symbol, "result": restore_result})
        symbol_results.append(
            {
                "symbol": symbol,
                "status": "failed_bracket_submit_stop_restore_attempted",
                "valid": False,
                "qty": qty,
                "old_stop_order_id": current_stop_order_id,
                "stop_price": stop_price,
                "take_profit_price": take_profit_price,
                "orders_changed": True,
                "rollback_attempted": True,
                "rollback_succeeded": _submit_succeeded(restore_result),
                "actions": actions,
            }
        )

    succeeded = [item for item in symbol_results if item.get("status") == "executed_paper_bracket_upgrade"]
    failed = [item for item in symbol_results if item.get("status") != "executed_paper_bracket_upgrade"]
    return {
        "status": "executed" if succeeded and not failed else "partial_failure" if succeeded else "failed",
        "mode": "approved_paper_bracket_upgrade_executor",
        "safety": "paper_only_order_mutation_after_manual_gate",
        "execution_enabled": True,
        "ticket_id": gate.get("ticket_id"),
        "orders_changed": any(item.get("orders_changed") for item in symbol_results),
        "summary": {
            "requested_symbol_count": len(validated),
            "executed_symbol_count": len(succeeded),
            "failed_symbol_count": len(failed),
            "orders_changed": any(item.get("orders_changed") for item in symbol_results),
        },
        "global_checks": global_checks,
        "symbols": symbol_results,
        "next_step": "verify_broker_snapshot_and_store_audit" if succeeded and not failed else "inspect_failed_symbol_and_restore_protection_if_needed",
    }
