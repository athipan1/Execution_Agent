import pytest

from app.adapters.alpaca_hydrated import HydratedAlpacaAdapter
from app.services.protection_diagnostics import build_protection_diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_legs", [None, []])
async def test_compound_parent_with_price_but_missing_legs_is_hydrated(
    monkeypatch,
    missing_legs,
):
    monkeypatch.setattr(
        "app.adapters.alpaca.settings.ALPACA_API_URL",
        "https://paper-api.alpaca.markets",
    )
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_KEY_ID", "key")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_SECRET_KEY", "secret")

    adapter = HydratedAlpacaAdapter()
    calls = []

    async def fake_get_json(path):
        calls.append(path)
        if path == "/v2/orders?status=open&limit=100&nested=true":
            return [
                {
                    "id": "parent-1",
                    "symbol": "ACGL",
                    "side": "sell",
                    "qty": "151",
                    "type": "limit",
                    "order_class": "bracket",
                    "status": "new",
                    "limit_price": "112.84",
                    "legs": missing_legs,
                }
            ]
        if path == "/v2/orders/parent-1?nested=true":
            return {
                "id": "parent-1",
                "symbol": "ACGL",
                "side": "sell",
                "qty": "151",
                "type": "limit",
                "order_class": "bracket",
                "status": "new",
                "limit_price": "112.84",
                "legs": [
                    {
                        "id": "stop-1",
                        "symbol": "ACGL",
                        "side": "sell",
                        "qty": "151",
                        "type": "stop",
                        "order_class": "bracket",
                        "status": "new",
                        "stop_price": "96.90",
                    }
                ],
            }
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    orders = await adapter.get_open_orders()

    assert calls == [
        "/v2/orders?status=open&limit=100&nested=true",
        "/v2/orders/parent-1?nested=true",
    ]
    assert orders[0]["legs"][0]["id"] == "stop-1"

    diagnostics = build_protection_diagnostics(
        [{"symbol": "ACGL", "qty": "151", "current_price": "101.06"}],
        orders,
    )
    assert diagnostics["positions"][0]["protection_status"] == "bracket_protected"
    assert diagnostics["positions"][0]["stop_covered_qty"] == 151.0
    assert diagnostics["positions"][0]["take_profit_covered_qty"] == 151.0

    await adapter._client.aclose()


@pytest.mark.asyncio
async def test_plain_limit_order_does_not_trigger_unnecessary_detail_fetch(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.alpaca.settings.ALPACA_API_URL",
        "https://paper-api.alpaca.markets",
    )
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_KEY_ID", "key")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_SECRET_KEY", "secret")

    adapter = HydratedAlpacaAdapter()
    calls = []

    async def fake_get_json(path):
        calls.append(path)
        return [
            {
                "id": "limit-1",
                "symbol": "MSFT",
                "side": "sell",
                "qty": "10",
                "type": "limit",
                "order_class": "simple",
                "status": "new",
                "limit_price": "500.00",
            }
        ]

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    orders = await adapter.get_open_orders()

    assert calls == ["/v2/orders?status=open&limit=100&nested=true"]
    assert orders[0]["id"] == "limit-1"

    await adapter._client.aclose()
