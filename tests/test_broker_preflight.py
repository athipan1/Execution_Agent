from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.db_client import InMemoryDatabaseClient
from app.models import CreateOrderRequest, OrderSide, OrderType, OrderStatus
from app.services.broker_preflight import BrokerPreflightError, validate_broker_preflight
from app.services.execution_service import ExecutionService


class FakeBroker:
    def __init__(self, *, account=None, positions=None, open_orders=None):
        self.account = account or {
            "broker": "TEST",
            "status": "ACTIVE",
            "cash": "10000",
            "buying_power": "10000",
            "equity": "10000",
            "trading_blocked": False,
            "transfers_blocked": False,
            "account_blocked": False,
        }
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.placed_orders = []

    async def get_account(self):
        return self.account

    async def get_positions(self):
        return self.positions

    async def get_open_orders(self):
        return self.open_orders

    async def place_order(self, order, update_callback):
        self.placed_orders.append(order)
        await update_callback({
            "order_id": order.order_id,
            "status": OrderStatus.PLACED,
            "broker_order_id": f"broker-{order.order_id}",
            "executed_quantity": 0,
        })

    async def get_order_status(self, broker_order_id: str):
        return {"status": OrderStatus.PLACED, "broker_order_id": broker_order_id, "executed_quantity": 0}

    async def cancel_order(self, broker_order_id: str):
        return {"status": OrderStatus.CANCELLED}

    async def execute(self, trade_order):
        raise NotImplementedError

    async def check_connection(self):
        return True


def order_request(trade_id: str, *, side=OrderSide.BUY, quantity=10, price=100.0):
    return CreateOrderRequest(
        trade_id=trade_id,
        account_id=1,
        symbol="AAPL",
        side=side,
        order_type=OrderType.MARKET,
        price=price,
        quantity=quantity,
        risk_approval_id="risk-test-approval",
        final_quantity=quantity,
        guard_plan={"symbol": "AAPL", "side": "sell" if side == OrderSide.BUY else "buy", "quantity": quantity, "trigger_price": 90},
    )


@pytest.mark.asyncio
async def test_preflight_rejects_buy_when_buying_power_zero_before_broker_submit():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-no-buying-power"))
    broker = FakeBroker(account={
        "broker": "ALPACA",
        "status": "ACTIVE",
        "cash": "-100223.4",
        "buying_power": "0",
        "equity": "99756.15",
        "trading_blocked": False,
        "transfers_blocked": False,
        "account_blocked": False,
    })
    service = ExecutionService(db, broker)

    updated = await service.start_order_execution(order)

    assert updated.status == OrderStatus.FAILED
    assert "buying_power_unavailable" in updated.reason
    assert broker.placed_orders == []


@pytest.mark.asyncio
async def test_preflight_rejects_stale_open_orders_before_broker_submit():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-stale-open-order"))
    stale_time = (datetime.now(timezone.utc) - timedelta(minutes=500)).isoformat()
    broker = FakeBroker(open_orders=[{"id": "old-order", "symbol": "AAPL", "submitted_at": stale_time, "status": "accepted"}])
    service = ExecutionService(db, broker)

    updated = await service.start_order_execution(order)

    assert updated.status == OrderStatus.FAILED
    assert "stale_open_orders_present" in updated.reason
    assert broker.placed_orders == []


@pytest.mark.asyncio
async def test_preflight_allows_healthy_broker_and_places_order():
    db = InMemoryDatabaseClient()
    order = await db.create_order(order_request("trade-healthy"))
    broker = FakeBroker()
    service = ExecutionService(db, broker)

    updated = await service.start_order_execution(order)

    assert updated.status == OrderStatus.PLACED
    assert updated.broker_order_id == f"broker-{order.order_id}"
    assert len(broker.placed_orders) == 1


def test_validate_broker_preflight_snapshot_has_buying_power_details():
    order = order_request("trade-snapshot", quantity=5, price=100).model_dump()
    from app.models import Order
    broker_order = Order(order_id=1, **order)

    snapshot = validate_broker_preflight(
        {
            "broker": "ALPACA",
            "status": "ACTIVE",
            "cash": "1000",
            "buying_power": "1000",
            "equity": "1000",
            "trading_blocked": False,
            "account_blocked": False,
        },
        positions=[],
        open_orders=[],
        order=broker_order,
    )

    assert snapshot["approved"] is True
    assert snapshot["buying_power"] == 1000.0
    assert snapshot["order_notional"] == 500.0
    assert snapshot["buying_power_after_order"] == 500.0
