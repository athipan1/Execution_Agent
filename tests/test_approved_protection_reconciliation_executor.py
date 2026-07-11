import pytest

from app.models import OrderStatus
from app.services.approved_protection_reconciliation_executor import (
    build_protection_reconciliation_ticket,
    execute_approved_paper_protection_reconciliation,
)
from app.services.protection_reconciliation import build_protection_reconciliation_preview


class RecordingAdapter:
    def __init__(self, *, submit_result=None, protected_after_submit=True):
        self.cancelled = []
        self.exit_brackets = []
        self.restored_stops = []
        self.submit_result = submit_result or {
            "status": OrderStatus.PLACED,
            "broker_order_id": "new-oco-1",
        }
        self.protected_after_submit = protected_after_submit

    async def cancel_order(self, broker_order_id: str) -> dict:
        self.cancelled.append(broker_order_id)
        return {"status": OrderStatus.CANCELLED, "broker_order_id": broker_order_id}

    async def submit_exit_bracket_order(self, **kwargs):
        self.exit_brackets.append(kwargs)
        return dict(self.submit_result)

    async def submit_protective_stop_order(self, **kwargs):
        self.restored_stops.append(kwargs)
        return {"status": OrderStatus.PLACED, "broker_order_id": "restored-stop-1"}

    async def get_positions(self):
        return [
            {
                "symbol": "ACGL",
                "qty": "151",
                "current_price": "102.20",
                "avg_entry_price": "99.96",
            }
        ]

    async def get_open_orders(self):
        if not self.protected_after_submit:
            return []
        return [
            {
                "id": "new-oco-1",
                "symbol": "ACGL",
                "side": "sell",
                "qty": "151",
                "type": "limit",
                "order_class": "oco",
                "status": "new",
                "limit_price": "116.60",
                "legs": [
                    {
                        "id": "new-stop-1",
                        "symbol": "ACGL",
                        "side": "sell",
                        "qty": "151",
                        "type": "stop",
                        "status": "new",
                        "stop_price": "95.00",
                    }
                ],
            }
        ]


def reconciliation_preview(existing_orders=True):
    diagnostics = {
        "positions": [
            {
                "symbol": "ACGL",
                "position_qty": "151",
                "current_price": "102.20",
                "protection_status": "partially_protected",
                "open_orders": (
                    [
                        {
                            "id": "old-tp-54",
                            "symbol": "ACGL",
                            "side": "sell",
                            "qty": "54",
                            "type": "limit",
                        }
                    ]
                    if existing_orders
                    else []
                ),
            }
        ]
    }
    proposals = [
        {
            "symbol": "ACGL",
            "qty": 151,
            "stop_price": 95.0,
            "take_profit_price": 116.6,
            "risk_policy_version": "risk-v1",
            "calculation_method": "approved_risk_proposal",
        }
    ]
    return build_protection_reconciliation_preview(diagnostics, proposals)


def execution_payload(preview):
    ticket = build_protection_reconciliation_ticket(preview)
    return {
        "execute_paper": True,
        "execution_confirmation_phrase": "EXECUTE_PAPER_PROTECTION_RECONCILIATION",
        "reconciliation_ticket_id": ticket["ticket_id"],
        "symbols": ["ACGL"],
    }


@pytest.mark.asyncio
async def test_partial_protection_is_replaced_and_verified():
    preview = reconciliation_preview(existing_orders=True)
    adapter = RecordingAdapter()

    result = await execute_approved_paper_protection_reconciliation(
        payload=execution_payload(preview),
        preview=preview,
        account={
            "paper": True,
            "status": "ACTIVE",
            "account_blocked": False,
            "trading_blocked": False,
        },
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "executed"
    assert result["summary"]["verified_symbol_count"] == 1
    assert adapter.cancelled == ["old-tp-54"]
    assert adapter.exit_brackets[0]["qty"] == "151"
    assert result["symbols"][0]["verification"]["verified"] is True


@pytest.mark.asyncio
async def test_unprotected_position_submits_without_cancellation():
    preview = reconciliation_preview(existing_orders=False)
    adapter = RecordingAdapter()

    result = await execute_approved_paper_protection_reconciliation(
        payload=execution_payload(preview),
        preview=preview,
        account={
            "paper": True,
            "status": "ACTIVE",
            "account_blocked": False,
            "trading_blocked": False,
        },
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "executed"
    assert adapter.cancelled == []
    assert len(adapter.exit_brackets) == 1


@pytest.mark.asyncio
async def test_stale_or_missing_ticket_blocks_before_broker_call():
    preview = reconciliation_preview()
    adapter = RecordingAdapter()

    result = await execute_approved_paper_protection_reconciliation(
        payload={
            "execute_paper": True,
            "execution_confirmation_phrase": "EXECUTE_PAPER_PROTECTION_RECONCILIATION",
            "reconciliation_ticket_id": "stale-ticket",
            "symbols": ["ACGL"],
        },
        preview=preview,
        account={
            "paper": True,
            "status": "ACTIVE",
            "account_blocked": False,
            "trading_blocked": False,
        },
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "blocked"
    assert adapter.cancelled == []
    assert adapter.exit_brackets == []


@pytest.mark.asyncio
async def test_live_mode_is_always_blocked():
    preview = reconciliation_preview()
    adapter = RecordingAdapter()

    result = await execute_approved_paper_protection_reconciliation(
        payload=execution_payload(preview),
        preview=preview,
        account={
            "paper": False,
            "status": "ACTIVE",
            "account_blocked": False,
            "trading_blocked": False,
        },
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="LIVE",
    )

    assert result["status"] == "blocked"
    assert adapter.cancelled == []
    assert adapter.exit_brackets == []


@pytest.mark.asyncio
async def test_failed_oco_submit_restores_full_position_stop():
    preview = reconciliation_preview()
    adapter = RecordingAdapter(
        submit_result={"status": OrderStatus.FAILED, "reason": "simulated failure"}
    )

    result = await execute_approved_paper_protection_reconciliation(
        payload=execution_payload(preview),
        preview=preview,
        account={
            "paper": True,
            "status": "ACTIVE",
            "account_blocked": False,
            "trading_blocked": False,
        },
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "failed"
    assert adapter.cancelled == ["old-tp-54"]
    assert adapter.restored_stops[0]["qty"] == "151"
    assert result["symbols"][0]["rollback_succeeded"] is True
    assert result["symbols"][0]["critical_protection_gap"] is False


@pytest.mark.asyncio
async def test_submit_without_verified_broker_legs_is_not_reported_as_success():
    preview = reconciliation_preview()
    adapter = RecordingAdapter(protected_after_submit=False)

    result = await execute_approved_paper_protection_reconciliation(
        payload=execution_payload(preview),
        preview=preview,
        account={
            "paper": True,
            "status": "ACTIVE",
            "account_blocked": False,
            "trading_blocked": False,
        },
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "failed"
    assert result["symbols"][0]["status"] == "submitted_verification_pending_or_failed"
    assert result["symbols"][0]["verification"]["verified"] is False
