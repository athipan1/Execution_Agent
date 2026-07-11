import pytest

from app.adapters.alpaca import AlpacaAdapter
from app.adapters.alpaca_hydrated import HydratedAlpacaAdapter
from app.models import OrderStatus


def configure_adapter(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.alpaca.settings.ALPACA_API_URL",
        "https://paper-api.alpaca.markets",
    )
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_KEY_ID", "key")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_SECRET_KEY", "secret")
    return HydratedAlpacaAdapter()


@pytest.mark.asyncio
async def test_cancel_waits_until_pending_cancel_becomes_canceled(monkeypatch):
    adapter = configure_adapter(monkeypatch)
    monkeypatch.setattr(
        "app.adapters.alpaca_hydrated.CANCEL_CONFIRMATION_INTERVAL_SECONDS",
        0,
    )

    async def fake_base_cancel(self, broker_order_id):
        return {
            "status": OrderStatus.CANCELLED,
            "broker_order_id": broker_order_id,
        }

    statuses = iter(["pending_cancel", "pending_cancel", "canceled"])
    observed = []

    async def fake_get_broker_order(broker_order_id):
        status = next(statuses)
        observed.append(status)
        return {"id": broker_order_id, "status": status}

    monkeypatch.setattr(AlpacaAdapter, "cancel_order", fake_base_cancel)
    monkeypatch.setattr(adapter, "get_broker_order", fake_get_broker_order)

    result = await adapter.cancel_order("old-tp-54")

    assert observed == ["pending_cancel", "pending_cancel", "canceled"]
    assert result["status"] == OrderStatus.CANCELLED
    assert result["cancel_requested"] is True
    assert result["cancel_confirmed"] is True
    assert result["broker_status"] == "canceled"
    assert result["confirmation_attempts"] == 3

    await adapter._client.aclose()


@pytest.mark.asyncio
async def test_cancel_confirmation_timeout_blocks_replacement_race(monkeypatch):
    adapter = configure_adapter(monkeypatch)
    monkeypatch.setattr(
        "app.adapters.alpaca_hydrated.CANCEL_CONFIRMATION_INTERVAL_SECONDS",
        0,
    )
    monkeypatch.setattr(
        "app.adapters.alpaca_hydrated.CANCEL_CONFIRMATION_ATTEMPTS",
        3,
    )

    async def fake_base_cancel(self, broker_order_id):
        return {
            "status": OrderStatus.CANCELLED,
            "broker_order_id": broker_order_id,
        }

    async def fake_get_broker_order(broker_order_id):
        return {"id": broker_order_id, "status": "pending_cancel"}

    monkeypatch.setattr(AlpacaAdapter, "cancel_order", fake_base_cancel)
    monkeypatch.setattr(adapter, "get_broker_order", fake_get_broker_order)

    result = await adapter.cancel_order("old-tp-54")

    assert result["status"] == "error"
    assert result["cancel_requested"] is True
    assert result["cancel_confirmed"] is False
    assert result["last_broker_status"] == "pending_cancel"
    assert result["confirmation_attempts"] == 3
    assert "insufficient-quantity race" in result["message"]

    await adapter._client.aclose()


@pytest.mark.asyncio
async def test_failed_cancel_request_does_not_poll_broker_status(monkeypatch):
    adapter = configure_adapter(monkeypatch)

    async def fake_base_cancel(self, broker_order_id):
        return {
            "status": "error",
            "broker_order_id": broker_order_id,
            "message": "cancel rejected",
        }

    async def unexpected_get_broker_order(_broker_order_id):
        raise AssertionError("status polling must not run after a rejected cancel")

    monkeypatch.setattr(AlpacaAdapter, "cancel_order", fake_base_cancel)
    monkeypatch.setattr(adapter, "get_broker_order", unexpected_get_broker_order)

    result = await adapter.cancel_order("old-tp-54")

    assert result["status"] == "error"
    assert result["message"] == "cancel rejected"

    await adapter._client.aclose()
