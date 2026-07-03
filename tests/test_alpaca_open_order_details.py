import pytest

from app.adapters.alpaca import AlpacaAdapter


@pytest.mark.asyncio
async def test_alpaca_get_open_orders_preserves_price_fields(monkeypatch):
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_KEY_ID", "key")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_SECRET_KEY", "secret")

    adapter = AlpacaAdapter()

    async def fake_get_json(path):
        assert path == "/v2/orders?status=open&limit=100&nested=true"
        return [
            {
                "id": "order-1",
                "symbol": "ACGL",
                "side": "sell",
                "qty": "82",
                "type": "stop",
                "order_class": "oto",
                "status": "new",
                "stop_price": "95.00",
                "limit_price": None,
                "trail_price": None,
                "trail_percent": None,
            }
        ]

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    orders = await adapter.get_open_orders()

    assert orders == [
        {
            "id": "order-1",
            "symbol": "ACGL",
            "side": "sell",
            "qty": "82",
            "type": "stop",
            "order_class": "oto",
            "status": "new",
            "stop_price": "95.00",
            "limit_price": None,
            "trail_price": None,
            "trail_percent": None,
            "submitted_at": None,
            "created_at": None,
            "legs": None,
        }
    ]

    await adapter._client.aclose()


@pytest.mark.asyncio
async def test_alpaca_get_open_orders_fetches_full_order_when_prices_are_missing(monkeypatch):
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_KEY_ID", "key")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_SECRET_KEY", "secret")

    adapter = AlpacaAdapter()
    calls = []

    async def fake_get_json(path):
        calls.append(path)
        if path == "/v2/orders?status=open&limit=100&nested=true":
            return [
                {
                    "id": "order-2",
                    "symbol": "ADBE",
                    "side": "sell",
                    "qty": "52",
                    "type": "stop",
                    "order_class": "oto",
                    "status": "new",
                }
            ]
        if path == "/v2/orders/order-2":
            return {
                "id": "order-2",
                "symbol": "ADBE",
                "side": "sell",
                "qty": "52",
                "type": "stop",
                "order_class": "oto",
                "status": "new",
                "stop_price": "200.00",
            }
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    orders = await adapter.get_open_orders()

    assert calls == ["/v2/orders?status=open&limit=100&nested=true", "/v2/orders/order-2"]
    assert orders[0]["symbol"] == "ADBE"
    assert orders[0]["stop_price"] == "200.00"
    assert orders[0]["limit_price"] is None

    await adapter._client.aclose()


@pytest.mark.asyncio
async def test_alpaca_get_open_orders_keeps_list_snapshot_when_detail_fetch_fails(monkeypatch):
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_API_KEY_ID", "key")
    monkeypatch.setattr("app.adapters.alpaca.settings.ALPACA_SECRET_KEY", "secret")

    adapter = AlpacaAdapter()

    async def fake_get_json(path):
        if path == "/v2/orders?status=open&limit=100&nested=true":
            return [
                {
                    "id": "order-3",
                    "symbol": "BKNG",
                    "side": "sell",
                    "qty": "47",
                    "type": "stop",
                    "order_class": "oto",
                    "status": "new",
                }
            ]
        if path == "/v2/orders/order-3":
            raise RuntimeError("detail unavailable")
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    orders = await adapter.get_open_orders()

    assert orders[0]["symbol"] == "BKNG"
    assert orders[0]["stop_price"] is None
    assert orders[0]["type"] == "stop"

    await adapter._client.aclose()
