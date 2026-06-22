from datetime import datetime, timedelta, timezone

import pytest

from app.services.broker_state_reconciliation import BrokerStateReconciliationService


class FakeBroker:
    async def get_account(self):
        return {
            "broker": "ALPACA",
            "paper": True,
            "status": "ACTIVE",
            "cash": "-100223.4",
            "buying_power": "0",
            "equity": "99909.6",
            "portfolio_value": "99909.6",
            "trading_blocked": False,
            "account_blocked": False,
        }

    async def get_positions(self):
        return [
            {"symbol": "AAPL", "qty": "1", "market_value": "295.5"},
            {"symbol": "ACGL", "qty": "2190", "market_value": "199837.5"},
        ]

    async def get_open_orders(self):
        return [
            {
                "id": "order-aapl",
                "symbol": "AAPL",
                "side": "sell",
                "qty": "1",
                "status": "new",
                "submitted_at": (datetime.now(timezone.utc) - timedelta(minutes=500)).isoformat(),
            }
        ]


@pytest.mark.asyncio
async def test_collect_broker_state_flags_zero_buying_power_and_negative_cash():
    service = BrokerStateReconciliationService(FakeBroker())

    result = await service.collect_broker_state(account_id=1)

    assert result["broker"] == "ALPACA"
    assert result["summary"]["position_count"] == 2
    assert result["summary"]["open_order_count"] == 1
    assert result["summary"]["stale_order_count"] == 1
    assert result["summary"]["buying_power_unavailable"] is True
    assert result["summary"]["cash_negative"] is True


@pytest.mark.asyncio
async def test_reconcile_skips_database_when_db_url_missing(monkeypatch):
    monkeypatch.setattr("app.services.broker_state_reconciliation.settings.DB_AGENT_URL", None)
    service = BrokerStateReconciliationService(FakeBroker())

    result = await service.reconcile(account_id=1, push_to_database=True)

    assert result["ok"] is True
    assert result["database_sync"]["status"] == "skipped"
    assert result["broker_state"]["summary"]["open_order_count"] == 1


@pytest.mark.asyncio
async def test_reconcile_can_skip_push_when_requested():
    service = BrokerStateReconciliationService(FakeBroker())

    result = await service.reconcile(account_id=1, push_to_database=False)

    assert result["ok"] is True
    assert result["database_sync"]["status"] == "skipped"
    assert result["database_sync"]["reason"] == "push_to_database=false"


@pytest.mark.asyncio
async def test_push_broker_state_to_database_success(monkeypatch, respx):
    monkeypatch.setattr("app.services.broker_state_reconciliation.settings.DB_AGENT_URL", "http://database-agent:8000")
    monkeypatch.setattr("app.services.broker_state_reconciliation.settings.BROKER_SYNC_ENDPOINT", "/broker-sync")
    route = respx.post("http://database-agent:8000/broker-sync").respond(200, json={"status": "success", "data": {"synced": True}})
    service = BrokerStateReconciliationService(FakeBroker())
    broker_state = await service.collect_broker_state(account_id=1)

    result = await service.push_broker_state_to_database(broker_state)

    assert route.called
    assert result["status"] == "success"
    assert result["http_status"] == 200


@pytest.mark.asyncio
async def test_push_broker_state_to_database_failure_does_not_drop_state(monkeypatch, respx):
    monkeypatch.setattr("app.services.broker_state_reconciliation.settings.DB_AGENT_URL", "http://database-agent:8000")
    monkeypatch.setattr("app.services.broker_state_reconciliation.settings.BROKER_SYNC_ENDPOINT", "/broker-sync")
    respx.post("http://database-agent:8000/broker-sync").respond(500, json={"status": "error"})
    service = BrokerStateReconciliationService(FakeBroker())
    broker_state = await service.collect_broker_state(account_id=1)

    result = await service.push_broker_state_to_database(broker_state)

    assert result["status"] == "failed"
    assert result["http_status"] == 500
