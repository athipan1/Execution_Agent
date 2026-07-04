import pytest

from app.models import OrderStatus
from app.services.approved_bracket_upgrade_executor import execute_approved_paper_bracket_upgrade


class RecordingAdapter:
    def __init__(self, *, submit_result=None):
        self.cancelled = []
        self.exit_brackets = []
        self.restored_stops = []
        self.submit_result = submit_result or {"status": OrderStatus.PLACED, "broker_order_id": "new-oco-1"}

    async def cancel_order(self, broker_order_id: str) -> dict:
        self.cancelled.append(broker_order_id)
        return {"status": OrderStatus.CANCELLED, "broker_order_id": broker_order_id}

    async def submit_exit_bracket_order(self, **kwargs):
        self.exit_brackets.append(kwargs)
        return dict(self.submit_result)

    async def submit_protective_stop_order(self, **kwargs):
        self.restored_stops.append(kwargs)
        return {"status": OrderStatus.PLACED, "broker_order_id": "restored-stop-1"}


def validated_gate():
    return {
        "status": "validated",
        "approval_valid": True,
        "ticket_id": "ticket-1",
        "symbols": [
            {
                "symbol": "BKNG",
                "valid": True,
                "qty": "47",
                "current_stop_order_id": "old-stop-1",
                "stop_price": 168.19,
                "take_profit_price": 217.30,
            }
        ],
    }


@pytest.mark.asyncio
async def test_execute_approved_paper_bracket_upgrade_replaces_one_validated_stop():
    adapter = RecordingAdapter()
    result = await execute_approved_paper_bracket_upgrade(
        payload={
            "execute_paper": True,
            "execution_confirmation_phrase": "EXECUTE_PAPER_BRACKET_UPGRADE",
        },
        gate=validated_gate(),
        account={"paper": True, "status": "ACTIVE", "account_blocked": False, "trading_blocked": False},
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "executed"
    assert result["orders_changed"] is True
    assert adapter.cancelled == ["old-stop-1"]
    assert adapter.exit_brackets[0]["symbol"] == "BKNG"
    assert adapter.exit_brackets[0]["qty"] == "47"
    assert adapter.exit_brackets[0]["stop_price"] == 168.19
    assert adapter.exit_brackets[0]["take_profit_price"] == 217.30
    assert adapter.restored_stops == []


@pytest.mark.asyncio
async def test_execute_approved_paper_bracket_upgrade_blocks_without_execution_phrase():
    adapter = RecordingAdapter()
    result = await execute_approved_paper_bracket_upgrade(
        payload={"execute_paper": True},
        gate=validated_gate(),
        account={"paper": True, "status": "ACTIVE", "account_blocked": False, "trading_blocked": False},
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "blocked"
    assert result["orders_changed"] is False
    assert adapter.cancelled == []
    assert adapter.exit_brackets == []


@pytest.mark.asyncio
async def test_execute_approved_paper_bracket_upgrade_restores_stop_when_submit_fails():
    adapter = RecordingAdapter(submit_result={"status": OrderStatus.FAILED, "reason": "simulated submit failure"})
    result = await execute_approved_paper_bracket_upgrade(
        payload={
            "execute_paper": True,
            "execution_confirmation_phrase": "EXECUTE_PAPER_BRACKET_UPGRADE",
        },
        gate=validated_gate(),
        account={"paper": True, "status": "ACTIVE", "account_blocked": False, "trading_blocked": False},
        adapter=adapter,
        broker_mode="ALPACA",
        trading_mode="PAPER",
    )

    assert result["status"] == "failed"
    assert result["orders_changed"] is True
    assert adapter.cancelled == ["old-stop-1"]
    assert adapter.restored_stops[0]["symbol"] == "BKNG"
    assert result["symbols"][0]["rollback_attempted"] is True
    assert result["symbols"][0]["rollback_succeeded"] is True
