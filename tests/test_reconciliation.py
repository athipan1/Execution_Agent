from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.db_client import InMemoryDatabaseClient
from app.models import CreateOrderRequest, OrderSide, OrderType, OrderStatus
from app.services.execution_service import ExecutionService


class FakeBroker:
    def __init__(self, status_payloads):
        self.status_payloads = status_payloads
        self.calls = []

    async def get_order_status(self, broker_order_id: str):
        self.calls.append(broker_order_id)
        return self.status_payloads[broker_order_id]

    async def place_order(self, order, update_callback):
        raise NotImplementedError

    async def cancel_order(self, broker_order_id: str):
        return {"status": OrderStatus.CANCELLED}

    async def execute(self, trade_order):
        raise NotImplementedError

    async def get_account(self):
        return {}

    async def get_positions(self):
        return []

    async def get_open_orders(self):
        return []

    async def check_connection(self):
        return True


def order_request(trade_id: str, symbol: str = "AAPL"):
    return CreateOrderRequest(
        trade_id=trade_id,
        account_id=1,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        price=100.0,
        quantity=10,
        risk_approval_id=f"risk-{trade_id}",
        final_quantity=10,
        guard_plan={"symbol": symbol, "side": "sell", "quantity": 10, "trigger_price": 90},
    )


@pytest.mark.asyncio
async def test_reconcile_updates_in_flight_order_from_broker():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-reconcile-1"))
    await db.update_order(order.order_id, {"status": OrderStatus.PLACED, "broker_order_id": "broker-1"})
    broker = FakeBroker({
        "broker-1": {
            "status": OrderStatus.EXECUTED,
            "broker_order_id": "broker-1",
            "executed_quantity": 10,
            "avg_execution_price": 101.5,
            "executed_at": datetime.now(timezone.utc),
        }
    })
    service = ExecutionService(db, broker)

    report = await service.reconcile_broker_orders()
    updated = await db.get_order_by_order_id(order.order_id)

    assert report.checked == 1
    assert report.updated == 1
    assert report.errors == 0
    assert report.items[0].action == "updated"
    assert updated.status == OrderStatus.EXECUTED
    assert updated.executed_quantity == 10


@pytest.mark.asyncio
async def test_reconcile_records_fill_delta_when_execution_quantity_increases():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-reconcile-fill"))
    await db.update_order(order.order_id, {"status": OrderStatus.PLACED, "broker_order_id": "broker-fill", "executed_quantity": 0})
    broker = FakeBroker({
        "broker-fill": {
            "status": OrderStatus.EXECUTED,
            "broker_order_id": "broker-fill",
            "executed_quantity": 10,
            "avg_execution_price": 101.5,
            "executed_at": datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc),
        }
    })
    service = ExecutionService(db, broker)

    report = await service.reconcile_broker_orders()

    assert report.updated == 1
    assert len(db.fills) == 1
    fill = db.fills[0]
    assert fill["order_id"] == order.order_id
    assert fill["trade_id"] == "trade-reconcile-fill"
    assert fill["symbol"] == "AAPL"
    assert fill["quantity"] == 10
    assert fill["fill_price"] == 101.5
    assert fill["broker_order_id"] == "broker-fill"


@pytest.mark.asyncio
async def test_reconcile_does_not_record_fill_when_quantity_unchanged():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-reconcile-no-fill"))
    await db.update_order(order.order_id, {"status": OrderStatus.PARTIALLY_FILLED, "broker_order_id": "broker-same", "executed_quantity": 5})
    broker = FakeBroker({
        "broker-same": {
            "status": OrderStatus.PARTIALLY_FILLED,
            "broker_order_id": "broker-same",
            "executed_quantity": 5,
            "avg_execution_price": 101.5,
            "executed_at": datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc),
        }
    })
    service = ExecutionService(db, broker)

    report = await service.reconcile_broker_orders()

    assert report.skipped == 1
    assert len(db.fills) == 0


@pytest.mark.asyncio
async def test_reconcile_records_only_incremental_partial_fill_delta():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-reconcile-partial-delta"))
    await db.update_order(order.order_id, {"status": OrderStatus.PARTIALLY_FILLED, "broker_order_id": "broker-delta", "executed_quantity": 4})
    broker = FakeBroker({
        "broker-delta": {
            "status": OrderStatus.EXECUTED,
            "broker_order_id": "broker-delta",
            "executed_quantity": 10,
            "avg_execution_price": 102.0,
            "executed_at": datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc),
        }
    })
    service = ExecutionService(db, broker)

    await service.reconcile_broker_orders()

    assert len(db.fills) == 1
    assert db.fills[0]["quantity"] == 6


@pytest.mark.asyncio
async def test_reconcile_skips_order_without_broker_id():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-reconcile-2"))
    broker = FakeBroker({})
    service = ExecutionService(db, broker)

    report = await service.reconcile_broker_orders()

    assert report.checked == 1
    assert report.skipped == 1
    assert report.updated == 0
    assert report.items[0].action == "skipped"
    assert broker.calls == []


@pytest.mark.asyncio
async def test_reconcile_records_broker_error():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-reconcile-3"))
    await db.update_order(order.order_id, {"status": OrderStatus.PLACED, "broker_order_id": "broker-error"})
    broker = FakeBroker({"broker-error": {"status": "error", "message": "broker unavailable"}})
    service = ExecutionService(db, broker)

    report = await service.reconcile_broker_orders()

    assert report.checked == 1
    assert report.errors == 1
    assert report.items[0].action == "error"
    assert "broker unavailable" in report.items[0].message
