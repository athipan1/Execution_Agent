from datetime import datetime, timedelta, timezone

import pytest

from app.db_client import InMemoryDatabaseClient
from app.models import (
    CreateOrderRequest,
    OrderSide,
    OrderType,
    RiskApproval,
    RiskApprovalStatus,
)
from app.services.execution_service import ExecutionService, RiskApprovalError


class NoopBroker:
    async def place_order(self, order, update_callback):
        return None

    async def get_order_status(self, broker_order_id: str):
        return {"status": "error", "message": "not implemented"}

    async def cancel_order(self, broker_order_id: str):
        return {"status": "cancelled"}

    async def execute(self, trade_order):
        return None

    async def get_account(self):
        return {}

    async def get_positions(self):
        return []

    async def get_open_orders(self):
        return []

    async def check_connection(self):
        return True


def approval(**overrides):
    data = {
        "approval_id": "risk-ok",
        "account_id": 1,
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "approved_quantity": 10,
        "status": RiskApprovalStatus.APPROVED,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    data.update(overrides)
    return RiskApproval(**data)


def request(**overrides):
    data = {
        "trade_id": "trade-ok",
        "account_id": 1,
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 10,
        "risk_approval_id": "risk-ok",
        "final_quantity": 10,
        "guard_plan": {"symbol": "AAPL", "side": "sell", "quantity": 10, "trigger_price": 90},
    }
    data.update(overrides)
    return CreateOrderRequest(**data)


@pytest.mark.asyncio
async def test_create_order_marks_risk_approval_used():
    db = InMemoryDatabaseClient()
    db.seed_risk_approval(approval())
    service = ExecutionService(db, NoopBroker())

    order = await service.create_order(request())
    used = await db.get_risk_approval("risk-ok")

    assert order.order_id == 1
    assert used.status == RiskApprovalStatus.USED
    assert used.order_id == order.order_id
    assert used.used_at is not None


@pytest.mark.asyncio
async def test_rejects_missing_risk_approval():
    service = ExecutionService(InMemoryDatabaseClient(), NoopBroker())

    with pytest.raises(RiskApprovalError, match="was not found"):
        await service.create_order(request())


@pytest.mark.asyncio
async def test_rejects_expired_risk_approval():
    db = InMemoryDatabaseClient()
    db.seed_risk_approval(approval(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    service = ExecutionService(db, NoopBroker())

    with pytest.raises(RiskApprovalError, match="expired"):
        await service.create_order(request())


@pytest.mark.asyncio
async def test_rejects_used_risk_approval_replay():
    db = InMemoryDatabaseClient()
    db.seed_risk_approval(approval(status=RiskApprovalStatus.USED))
    service = ExecutionService(db, NoopBroker())

    with pytest.raises(RiskApprovalError, match="not approved"):
        await service.create_order(request())


@pytest.mark.asyncio
async def test_rejects_mismatched_symbol_side_account_or_quantity():
    mismatch_cases = [
        (approval(symbol="MSFT"), request(), "symbol"),
        (approval(side=OrderSide.SELL), request(), "side"),
        (approval(account_id=2), request(), "account_id"),
        (approval(approved_quantity=5), request(), "quantity"),
    ]

    for approval_row, order_request, message in mismatch_cases:
        db = InMemoryDatabaseClient()
        db.seed_risk_approval(approval_row)
        service = ExecutionService(db, NoopBroker())
        with pytest.raises(RiskApprovalError, match=message):
            await service.create_order(order_request)
