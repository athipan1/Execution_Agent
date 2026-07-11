from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional

from app.services.protection_diagnostics import build_protection_diagnostics

EXECUTION_CONFIRMATION_PHRASE = "EXECUTE_PAPER_PROTECTION_RECONCILIATION"
FULLY_PROTECTED_STATUSES = {"bracket_protected", "tp_sl_protected"}
READY_STATUS = "ready_for_manual_reconciliation_review"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _symbol(value: Any) -> str:
    return _text(value).upper()


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    return _text(raw).lower()


def _check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _cancel_succeeded(result: Mapping[str, Any]) -> bool:
    return _status(result.get("status")) in {"cancelled", "canceled", "success"}


def _submit_succeeded(result: Mapping[str, Any]) -> bool:
    return _status(result.get("status")) in {"placed", "accepted", "new", "success"} or bool(
        result.get("broker_order_id")
    )


def _ticket_material(preview: Mapping[str, Any]) -> List[Dict[str, Any]]:
    material: List[Dict[str, Any]] = []
    for plan in preview.get("plans") or []:
        if not isinstance(plan, dict) or plan.get("preview_status") != READY_STATUS:
            continue
        material.append(
            {
                "symbol": _symbol(plan.get("symbol")),
                "position_qty": _text(plan.get("position_qty")),
                "current_status": _status(plan.get("current_status")),
                "stop_price": plan.get("stop_price"),
                "take_profit_price": plan.get("take_profit_price"),
                "existing_open_order_ids": sorted(
                    _text(order_id)
                    for order_id in plan.get("existing_open_order_ids") or []
                    if _text(order_id)
                ),
                "risk_policy_version": plan.get("risk_policy_version"),
                "calculation_method": plan.get("calculation_method"),
            }
        )
    return sorted(material, key=lambda item: item["symbol"])


def build_protection_reconciliation_ticket(preview: Mapping[str, Any]) -> Dict[str, Any]:
    material = _ticket_material(preview)
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return {
        "ticket_id": f"protection-reconciliation-{digest[:24]}",
        "ticket_hash": digest,
        "confirmation_phrase": EXECUTION_CONFIRMATION_PHRASE,
        "ready_symbol_count": len(material),
        "symbols": [item["symbol"] for item in material],
        "plans": material,
        "execution_scope": "alpaca_paper_only",
        "manual_approval_required": True,
    }


def _selected_ready_plans(
    preview: Mapping[str, Any], selected_symbols: Iterable[Any]
) -> List[Dict[str, Any]]:
    requested = {_symbol(value) for value in selected_symbols if _symbol(value)}
    plans = [
        dict(plan)
        for plan in preview.get("plans") or []
        if isinstance(plan, dict)
        and plan.get("preview_status") == READY_STATUS
        and (not requested or _symbol(plan.get("symbol")) in requested)
    ]
    return sorted(plans, key=lambda item: _symbol(item.get("symbol")))


def _find_diagnostic_row(diagnostics: Mapping[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    for row in diagnostics.get("positions") or []:
        if isinstance(row, dict) and _symbol(row.get("symbol")) == symbol:
            return row
    return None


async def _verify_symbol(adapter: Any, symbol: str) -> Dict[str, Any]:
    positions = await adapter.get_positions()
    open_orders = await adapter.get_open_orders()
    diagnostics = build_protection_diagnostics(positions, open_orders)
    row = _find_diagnostic_row(diagnostics, symbol)
    protection_status = _status((row or {}).get("protection_status"))
    return {
        "verified": protection_status in FULLY_PROTECTED_STATUSES,
        "protection_status": protection_status or "position_not_found",
        "diagnostic": row,
    }


async def execute_approved_paper_protection_reconciliation(
    *,
    payload: Optional[Dict[str, Any]],
    preview: Dict[str, Any],
    account: Dict[str, Any],
    adapter: Any,
    broker_mode: str,
    trading_mode: str,
) -> Dict[str, Any]:
    """Replace incomplete protection with full-position OCO orders on Alpaca Paper.

    The function is intentionally fail-closed. It requires an exact ticket built
    from the latest broker snapshot, an explicit confirmation phrase, explicit
    selected symbols, and a paper account. It never runs against a live account.
    """
    safe_payload = payload if isinstance(payload, dict) else {}
    ticket = build_protection_reconciliation_ticket(preview)
    selected_symbols = safe_payload.get("symbols") or []
    selected_plans = _selected_ready_plans(preview, selected_symbols)
    allow_multi_symbol = safe_payload.get("allow_multi_symbol") is True

    global_checks = [
        _check(
            "ticket_matches_latest_snapshot",
            _text(safe_payload.get("reconciliation_ticket_id")) == ticket["ticket_id"],
            "reconciliation_ticket_id must match the latest broker-backed preview",
        ),
        _check(
            "execution_confirmation_phrase",
            safe_payload.get("execution_confirmation_phrase") == EXECUTION_CONFIRMATION_PHRASE,
            "execution confirmation phrase must match",
        ),
        _check("execute_paper", safe_payload.get("execute_paper") is True, "execute_paper must be true"),
        _check("broker_mode", _text(broker_mode).upper() == "ALPACA", "broker mode must be ALPACA"),
        _check("trading_mode", _text(trading_mode).upper() == "PAPER", "executor is paper-only"),
        _check("paper_account", account.get("paper") is True, "account must be paper"),
        _check(
            "account_active",
            _text(account.get("status")).upper() == "ACTIVE",
            "account must be ACTIVE",
        ),
        _check("account_not_blocked", account.get("account_blocked") is not True, "account must not be blocked"),
        _check("trading_not_blocked", account.get("trading_blocked") is not True, "trading must not be blocked"),
        _check("selected_symbols_present", bool(selected_symbols), "symbols must be explicitly selected"),
        _check("ready_plans_present", bool(selected_plans), "selected symbols must have ready reconciliation plans"),
        _check(
            "single_symbol_default",
            allow_multi_symbol or len(selected_plans) <= 1,
            "multi-symbol execution requires allow_multi_symbol=true",
        ),
    ]

    if not all(check["passed"] for check in global_checks):
        return {
            "status": "blocked",
            "mode": "approved_paper_protection_reconciliation",
            "safety": "paper_only_order_mutation_blocked_before_broker_call",
            "execution_enabled": False,
            "orders_changed": False,
            "ticket": ticket,
            "global_checks": global_checks,
            "symbols": [],
            "next_step": "refresh_preview_fix_checks_and_confirm_again",
        }

    symbol_results: List[Dict[str, Any]] = []
    for plan in selected_plans:
        symbol = _symbol(plan.get("symbol"))
        qty = _text(plan.get("position_qty"))
        stop_price = plan.get("stop_price")
        take_profit_price = plan.get("take_profit_price")
        existing_order_ids = [
            _text(order_id)
            for order_id in plan.get("existing_open_order_ids") or []
            if _text(order_id)
        ]
        actions: List[Dict[str, Any]] = []
        cancelled_ids: List[str] = []
        cancel_failed = False

        for order_id in existing_order_ids:
            cancel_result = await adapter.cancel_order(order_id)
            actions.append(
                {
                    "action": "cancel_existing_protective_order",
                    "broker_order_id": order_id,
                    "result": cancel_result,
                }
            )
            if not _cancel_succeeded(cancel_result):
                cancel_failed = True
                break
            cancelled_ids.append(order_id)

        if cancel_failed:
            symbol_results.append(
                {
                    "symbol": symbol,
                    "status": "blocked_cancel_failed",
                    "valid": False,
                    "orders_changed": bool(cancelled_ids),
                    "cancelled_order_ids": cancelled_ids,
                    "critical_protection_gap": bool(cancelled_ids),
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
            client_order_id=f"protect-reconcile-{ticket['ticket_hash'][:12]}-{symbol}"[:48],
        )
        actions.append(
            {
                "action": "submit_full_position_oco",
                "symbol": symbol,
                "result": submit_result,
            }
        )

        if not _submit_succeeded(submit_result):
            restore_result = await adapter.submit_protective_stop_order(
                symbol=symbol,
                qty=qty,
                side="sell",
                stop_price=stop_price,
                client_order_id=f"restore-stop-{ticket['ticket_hash'][:12]}-{symbol}"[:48],
            )
            actions.append(
                {
                    "action": "rollback_restore_full_position_stop",
                    "symbol": symbol,
                    "result": restore_result,
                }
            )
            rollback_succeeded = _submit_succeeded(restore_result)
            symbol_results.append(
                {
                    "symbol": symbol,
                    "status": "failed_oco_submit_stop_restore_attempted",
                    "valid": False,
                    "orders_changed": bool(cancelled_ids) or rollback_succeeded,
                    "cancelled_order_ids": cancelled_ids,
                    "rollback_attempted": True,
                    "rollback_succeeded": rollback_succeeded,
                    "critical_protection_gap": not rollback_succeeded,
                    "actions": actions,
                }
            )
            continue

        verification = await _verify_symbol(adapter, symbol)
        actions.append({"action": "verify_full_position_protection", "result": verification})
        symbol_results.append(
            {
                "symbol": symbol,
                "status": (
                    "executed_and_verified"
                    if verification["verified"]
                    else "submitted_verification_pending_or_failed"
                ),
                "valid": verification["verified"],
                "qty": qty,
                "cancelled_order_ids": cancelled_ids,
                "new_oco_order_id": submit_result.get("broker_order_id"),
                "stop_price": stop_price,
                "take_profit_price": take_profit_price,
                "orders_changed": True,
                "rollback_attempted": False,
                "verification": verification,
                "critical_protection_gap": False,
                "actions": actions,
            }
        )

    verified = [item for item in symbol_results if item.get("status") == "executed_and_verified"]
    failed = [item for item in symbol_results if item.get("status") != "executed_and_verified"]
    critical = [item for item in symbol_results if item.get("critical_protection_gap")]
    return {
        "status": "executed" if verified and not failed else "partial_failure" if verified else "failed",
        "mode": "approved_paper_protection_reconciliation",
        "safety": "paper_only_order_mutation_after_latest_ticket_confirmation",
        "execution_enabled": True,
        "orders_changed": any(item.get("orders_changed") for item in symbol_results),
        "critical_protection_gap": bool(critical),
        "ticket": ticket,
        "global_checks": global_checks,
        "summary": {
            "requested_symbol_count": len(selected_plans),
            "verified_symbol_count": len(verified),
            "failed_symbol_count": len(failed),
            "critical_gap_count": len(critical),
        },
        "symbols": symbol_results,
        "next_step": (
            "protection_reconciliation_complete"
            if verified and not failed
            else "inspect_failures_and_restore_protection_immediately"
        ),
    }
