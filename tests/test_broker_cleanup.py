from datetime import datetime, timedelta, timezone

import pytest

from app.models import OrderStatus
from app.services.broker_cleanup import BrokerCleanupService, classify_open_orders, is_stale_order


class FakeCleanupBroker:
    def __init__(self, *, open_orders=None):
        self.open_orders = open_orders or []
        self.cancelled_ids = []

    async def get_account(self):
        return {
            "broker": "ALPACA",
            "paper": True,
            "status": "ACTIVE",
            "cash": "-100223.4",
            "buying_power": "0",
            "equity": "99757.56",
        }

    async def get_positions(self):
        return [{"symbol": "AAPL", "qty": "1"}, {"symbol": "ACGL", "qty": "2190"}]

    async def get_open_orders(self):
        return self.open_orders

    async def cancel_order(self, broker_order_id: str):
        self.cancelled_ids.append(broker_order_id)
        return {"status": OrderStatus.CANCELLED, "broker_order_id": broker_order_id}

    async def cancel_open_order(self, broker_order):
        return await self.cancel_order(broker_order["id"])


def stale_order(order_id="old-1", minutes=500):
    return {
        "id": order_id,
        "symbol": "ACGL",
        "side": "sell",
        "qty": "2190",
        "status": "accepted",
        "submitted_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
    }


def fresh_order(order_id="fresh-1", minutes=5):
    return {
        "id": order_id,
        "symbol": "AAPL",
        "side": "sell",
        "qty": "1",
        "status": "accepted",
        "submitted_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
    }


def test_is_stale_order_detects_old_submitted_order():
    assert is_stale_order(stale_order(minutes=500), max_age_minutes=390) is True
    assert is_stale_order(fresh_order(minutes=5), max_age_minutes=390) is False


def test_classify_open_orders_splits_stale_and_fresh():
    result = classify_open_orders([stale_order(), fresh_order()], max_age_minutes=390)

    assert result["open_order_count"] == 2
    assert result["stale_order_count"] == 1
    assert result["fresh_order_count"] == 1
    assert result["stale_orders"][0]["id"] == "old-1"


@pytest.mark.asyncio
async def test_cleanup_status_flags_cleanup_required_when_stale_orders_and_zero_buying_power():
    broker = FakeCleanupBroker(open_orders=[stale_order(), fresh_order()])
    service = BrokerCleanupService(broker)

    result = await service.cleanup_status(max_age_minutes=390)

    assert result["cleanup_required"] is True
    assert result["buying_power"] == "0"
    assert result["position_count"] == 2
    assert result["stale_order_count"] == 1


@pytest.mark.asyncio
async def test_cancel_stale_orders_dry_run_does_not_call_broker_cancel():
    broker = FakeCleanupBroker(open_orders=[stale_order(), fresh_order()])
    service = BrokerCleanupService(broker)

    result = await service.cancel_stale_open_orders(max_age_minutes=390, dry_run=True)

    assert result["dry_run"] is True
    assert result["would_cancel_count"] == 1
    assert result["cancelled_count"] == 0
    assert broker.cancelled_ids == []


@pytest.mark.asyncio
async def test_cancel_stale_orders_calls_broker_cancel_when_not_dry_run():
    broker = FakeCleanupBroker(open_orders=[stale_order(), fresh_order()])
    service = BrokerCleanupService(broker)

    result = await service.cancel_stale_open_orders(max_age_minutes=390, dry_run=False)

    assert result["dry_run"] is False
    assert result["matched_count"] == 1
    assert result["cancelled_count"] == 1
    assert result["failed_count"] == 0
    assert broker.cancelled_ids == ["old-1"]


@pytest.mark.asyncio
async def test_cancel_all_open_orders_can_dry_run_and_cancel_all():
    broker = FakeCleanupBroker(open_orders=[stale_order(), fresh_order()])
    service = BrokerCleanupService(broker)

    dry_run = await service.cancel_all_open_orders(dry_run=True)
    assert dry_run["would_cancel_count"] == 2
    assert broker.cancelled_ids == []

    real = await service.cancel_all_open_orders(dry_run=False)
    assert real["cancelled_count"] == 2
    assert broker.cancelled_ids == ["old-1", "fresh-1"]
